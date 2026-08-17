"""账号批量验活任务管理器 (支持 Token 验活与套餐/试用资格验活双模式)。

支持并发批量检测：
  1. Token 验活 (mode='token'):
     - 校验 access_token 是否有效 / 401 过期 / 403 封号 (account_deactivated)
     - 本地与远端双重校验，解析 JWT claims (exp, user_id, user_name)
     - 终态标记：token_valid (Token正常有效) / token_invalid (凭证失效) / banned (已封号) / error (网络异常)
     - 自动回写 extra_json.token_check / plus_check 与更新时间
  2. 套餐验活 (mode='plan'):
     - 深度请求 /backend-api/accounts/check 接口
     - 深度识别：Pro 20x / Pro 5x / Pro / Team / Plus 活跃 / 可领Plus 1个月免单试用 / Free / 封号 / 失效
     - 自动回写 extra_json.plus_check
"""
from __future__ import annotations

import base64
import json
import logging
import queue
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import quote

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:
    CurlSession = None

try:
    from . import db
    from .proxy_util import (
        COUNTRY_LANG_MAP,
        new_proxy_session_id,
        resolve_target_country,
        route_proxy_country,
    )
    from .plus_check import (
        CHECK_URL,
        DEFAULT_UA,
        _looks_deactivated,
        _decode_jwt_payload,
        _get_auth,
        get_country_timezone_offset_min,
        parse_account_plan,
        normalize_proxy,
        set_proxy,
        safe_close,
    )
except ImportError:
    import db
    from proxy_util import (
        COUNTRY_LANG_MAP,
        new_proxy_session_id,
        resolve_target_country,
        route_proxy_country,
    )
    from plus_check import (
        CHECK_URL,
        DEFAULT_UA,
        _looks_deactivated,
        _decode_jwt_payload,
        _get_auth,
        get_country_timezone_offset_min,
        parse_account_plan,
        normalize_proxy,
        set_proxy,
        safe_close,
    )

logger = logging.getLogger(__name__)


class HealthCheckTask:
    """单个批量验活任务。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config
        self.mode = str(config.get("mode") or "token").strip().lower()  # token / plan
        self.proxies: list[str] = config.get("proxies") or []
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        self.started_at = time.time()
        self.finished_at = 0.0

        # email -> item dict
        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "mode": self.mode,
                "status": "pending",
                "step_text": "排队中...",
                "result": None,
                "started_at": 0.0,
                "finished_at": 0.0,
                "elapsed": 0.0,
                "logs": [],
            }
            for e in emails
        }
        self.queue: queue.Queue = queue.Queue()
        self.cancelled = False
        self.done_count = 0
        self.stats = {
            "token_valid": 0,
            "token_invalid": 0,
            "banned": 0,
            "plus_active": 0,
            "plus_eligible": 0,
            "pro_active": 0,
            "team_active": 0,
            "free": 0,
            "error": 0,
        }
        self._lock = threading.Lock()

    def next_proxy(self) -> str:
        if not self.proxies:
            return ""
        with self._idx_lock:
            p = self.proxies[self._proxy_idx % len(self.proxies)]
            self._proxy_idx += 1
            return p

    def add_email_log(self, email: str, line: str) -> None:
        ts_str = time.strftime("%H:%M:%S")
        formatted = f"{ts_str} {line}"
        with self._lock:
            if email in self.items:
                self.items[email]["logs"].append(formatted)
                if len(self.items[email]["logs"]) > 250:
                    self.items[email]["logs"] = self.items[email]["logs"][-250:]
        try:
            self.queue.put({"kind": "log", "email": email, "line": f"[{email}] {line}"})
        except Exception:
            pass

    def set_running(self, email: str, step_text: str = "正在请求 ChatGPT 接口...") -> None:
        now = time.time()
        with self._lock:
            if email in self.items:
                self.items[email]["status"] = "running"
                self.items[email]["step_text"] = step_text
                self.items[email]["started_at"] = now
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "step_text": step_text,
            "started_at": now,
        })

    def mark_done(self, email: str, result: dict) -> None:
        now = time.time()
        with self._lock:
            self.done_count += 1
            st = result.get("status") or "error"
            if st in self.stats:
                self.stats[st] += 1
            elif "pro" in st:
                self.stats["pro_active"] += 1
            else:
                self.stats["error"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = "done"
                it["result"] = result
                it["step_text"] = result.get("label") or st
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "done",
            "step_text": result.get("label") or result.get("status"),
            "result": result,
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_tasks: dict[str, HealthCheckTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 20


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-10]
        for k in oldest_keys:
            _tasks.pop(k, None)


def _check_token_mode(task: HealthCheckTask, email: str, cred: dict, at: str, proxy: str, target_country: str) -> dict:
    """模式 1：Token 验活（快速验证 Token 是否有效 / 过期 / 封号）。"""
    task.add_email_log(email, "【Token 验活】正在解析 JWT 凭证与声明...")

    claims = _decode_jwt_payload(at)
    exp = claims.get("exp")
    exp_str = ""
    is_jwt_expired = False
    if isinstance(exp, (int, float)):
        exp_dt = datetime.fromtimestamp(exp, tz=timezone.utc)
        exp_str = exp_dt.strftime("%Y-%m-%d %H:%M:%S UTC")
        is_jwt_expired = time.time() >= float(exp)
        if is_jwt_expired:
            task.add_email_log(email, f"⚠️ 本地 JWT 已过期 (exp={exp_str})")

    # 请求远端验证 session 连通性
    sess = None
    timeout = float(task.config.get("timeout") or 20.0)
    started_req = time.time()

    try:
        if CurlSession is not None:
            sess = CurlSession(impersonate="chrome136")
        else:
            from http_client import create_http_session
            sess = create_http_session(proxy=proxy or None, impersonate="chrome110")

        if hasattr(sess, "trust_env"):
            sess.trust_env = False
        set_proxy(sess, proxy)

        headers = {
            "Authorization": f"Bearer {at}",
            "Accept": "application/json",
            "User-Agent": DEFAULT_UA,
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
        }
        if target_country and target_country in COUNTRY_LANG_MAP:
            headers["Accept-Language"] = COUNTRY_LANG_MAP[target_country]

        tz = get_country_timezone_offset_min(target_country or cred.get("reg_country") or "JP")
        url = f"https://chatgpt.com/backend-api/me"

        task.add_email_log(email, f"发送鉴权请求 GET {url}...")
        resp = sess.get(url, headers=headers, timeout=timeout)
        req_ms = int((time.time() - started_req) * 1000)
        status_code = resp.status_code
        task.add_email_log(email, f"收到响应 HTTP {status_code} ({req_ms}ms)")

        body = (resp.text or "").strip()

        if status_code == 200:
            user_data = resp.json() if body.startswith("{") else {}
            name = user_data.get("name") or user_data.get("email") or ""
            user_id = user_data.get("id") or ""
            task.add_email_log(email, f"🎉 Token 验证有效！用户名={name}, ID={user_id}")

            res = {
                "status": "token_valid",
                "label": "✅ Token正常",
                "mode": "token",
                "name": name,
                "user_id": user_id,
                "exp": exp_str,
                "checked_at": time.time(),
            }

            # 增量更新数据库
            db.update_plus_check(email, {
                "status": "token_valid",
                "label": "Token正常",
                "checked_at": time.time(),
            })
            return res

        if status_code in (401, 403):
            if _looks_deactivated(body):
                res = {
                    "status": "banned",
                    "label": "🚫 封号",
                    "mode": "token",
                    "error": f"HTTP {status_code} 账号已被禁用",
                    "checked_at": time.time(),
                }
                task.add_email_log(email, f"检测结论: 封号 (HTTP {status_code}) -> {body[:100]}")
                db.update_plus_check(email, res)
                return res
            if status_code == 401:
                res = {
                    "status": "token_invalid",
                    "label": "❌ Token失效(401)",
                    "mode": "token",
                    "error": "401 Unauthorized",
                    "checked_at": time.time(),
                }
                task.add_email_log(email, "检测结论: Token 已失效/被吊销 (401)")
                db.update_plus_check(email, res)
                return res

        err_msg = f"HTTP {status_code}: {body[:100]}"
        task.add_email_log(email, f"检测异常: {err_msg}")
        return {"status": "error", "label": f"HTTP {status_code}", "mode": "token", "error": err_msg}

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        task.add_email_log(email, f"网络请求异常: {err_msg}")
        return {"status": "error", "label": "请求异常", "mode": "token", "error": err_msg}
    finally:
        safe_close(sess)


def _check_plan_mode(task: HealthCheckTask, email: str, cred: dict, at: str, proxy: str, target_country: str) -> dict:
    """模式 2：套餐/试用资格深度验活。"""
    task.add_email_log(email, "【套餐验活】正在向 accounts/check 查询订阅与 0元 试用活动...")

    auth_claims = _get_auth(_decode_jwt_payload(at))
    account_id = str(auth_claims.get("chatgpt_account_id") or auth_claims.get("account_id") or "").strip()
    device_id = (cred.get("device_id") or "").strip() or str(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"yhm-check-plan:{email}")
    )

    sess = None
    timeout = float(task.config.get("timeout") or 20.0)
    started_req = time.time()

    try:
        if CurlSession is not None:
            sess = CurlSession(impersonate="chrome136")
        else:
            from http_client import create_http_session
            sess = create_http_session(proxy=proxy or None, impersonate="chrome110")

        if hasattr(sess, "trust_env"):
            sess.trust_env = False
        set_proxy(sess, proxy)

        headers = {
            "Authorization": f"Bearer {at}",
            "Accept": "application/json",
            "User-Agent": DEFAULT_UA,
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
        }
        if target_country and target_country in COUNTRY_LANG_MAP:
            headers["Accept-Language"] = COUNTRY_LANG_MAP[target_country]
        if account_id:
            headers["ChatGPT-Account-ID"] = account_id
        if device_id:
            headers["OAI-Device-Id"] = device_id

        tz = get_country_timezone_offset_min(target_country or cred.get("reg_country") or "JP")
        url_with_tz = f"{CHECK_URL}?timezone_offset_min={tz}"
        task.add_email_log(email, f"发送 GET {url_with_tz}...")
        resp = sess.get(url_with_tz, headers=headers, timeout=timeout)
        req_ms = int((time.time() - started_req) * 1000)
        status_code = resp.status_code
        task.add_email_log(email, f"收到响应 HTTP {status_code} ({req_ms}ms)")

        body = (resp.text or "").strip()

        if status_code in (401, 403):
            if _looks_deactivated(body):
                res = {"status": "banned", "label": "封号", "error": f"HTTP {status_code} 账号被禁用"}
            elif status_code == 401:
                res = {"status": "token_invalid", "label": "凭证失效", "error": "401 Unauthorized"}
            else:
                res = {"status": "error", "label": f"HTTP {status_code}", "error": f"HTTP {status_code}: {body[:100]}"}
            task.add_email_log(email, f"检测结论: {res.get('label')}")
            db.update_plus_check(email, res)
            return res

        if status_code == 200:
            try:
                data = resp.json()
            except Exception:
                data = json.loads(body)
            parsed = parse_account_plan(data, body)
            task.add_email_log(email, f"🎉 检测结论: {parsed.get('label')} (plan={parsed.get('plan')})")
            db.update_plus_check(email, parsed)
            return parsed

        err_msg = f"HTTP {status_code}: {body[:100]}"
        task.add_email_log(email, f"检测异常: {err_msg}")
        return {"status": "error", "label": f"HTTP {status_code}", "error": err_msg}

    except Exception as e:
        err_msg = f"{type(e).__name__}: {e}"
        task.add_email_log(email, f"网络请求异常: {err_msg}")
        return {"status": "error", "label": "请求异常", "error": err_msg}
    finally:
        safe_close(sess)


def _check_one_account(task: HealthCheckTask, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被中止"})
        return

    task.set_running(email, "读取账号凭证与准备网络代理...")
    task.add_email_log(email, f"开始验活 (mode={task.mode}): {email}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "not_found", "label": "未找到", "error": "数据库中无此凭证"}
        task.add_email_log(email, "错误: 数据库中无此凭证记录")
        task.mark_done(email, res)
        return

    at = (cred.get("access_token") or "").strip()
    if not at:
        res = {"status": "no_at", "label": "无AT", "error": "该账号缺少 access_token"}
        task.add_email_log(email, "错误: 账号缺少 access_token，无法发起鉴权")
        task.mark_done(email, res)
        return

    proxy = task.next_proxy()
    raw_country = (task.config.get("proxy_country") or "").strip().upper()
    target_country = resolve_target_country(raw_country)
    if proxy and target_country:
        proxy = route_proxy_country(proxy, target_country, new_proxy_session_id())

    proxy_label = proxy.split("@")[-1] if "@" in proxy else (proxy or "直连")
    country_tip = f" (目标国家: {target_country})" if target_country else ""
    task.add_email_log(email, f"使用代理: {proxy_label}{country_tip}")

    if task.mode == "token":
        result = _check_token_mode(task, email, cred, at, proxy, target_country)
    else:
        result = _check_plan_mode(task, email, cred, at, proxy, target_country)

    task.mark_done(email, result)


def start_health_check(emails: list[str], config: dict) -> str:
    """启动批量验活任务 (Token 验活 或 套餐验活)。"""
    cleaned = []
    seen = set()
    for e in emails:
        c = (e or "").strip().lower()
        if c and c not in seen:
            seen.add(c)
            cleaned.append(c)
    if not cleaned:
        raise ValueError("请提供至少一个待验活的账号")

    task_id = f"health_{uuid.uuid4().hex[:8]}"
    task = HealthCheckTask(task_id, cleaned, config)

    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    workers = max(1, min(10, int(config.get("workers") or 5)))

    def _runner():
        q: queue.Queue = queue.Queue()
        for e in cleaned:
            q.put(e)

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"health-{task_id}") as pool:
            futures = []
            for _ in range(workers):
                def _worker():
                    while not task.cancelled:
                        try:
                            em = q.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            _check_one_account(task, em)
                        finally:
                            q.task_done()
                futures.append(pool.submit(_worker))
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass

        task.finished_at = time.time()
        task.queue.put({"kind": "end"})

    threading.Thread(target=_runner, daemon=True, name=f"health-check-{task_id}").start()
    return task_id


def stop_health_check(task_id: str) -> bool:
    with _tasks_lock:
        t = _tasks.get(task_id)
        if not t:
            return False
        t.cancelled = True
        t.queue.put({"kind": "end"})
        return True


def get_task(task_id: str) -> Optional[HealthCheckTask]:
    with _tasks_lock:
        return _tasks.get(task_id)
