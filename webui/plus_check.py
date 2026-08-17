"""Plus 状态检测任务管理器（并发检测账号 Plus 试用、活跃订阅、Free、封号及凭证状态）。

对注册结果中的账号批量进行 Plus 状态与可用性检测：
    - 每个账号用自己的 access_token 请求 ChatGPT backend-api/accounts/check 接口
    - 判断账号状态：Plus生效中(plus_active) / 可领Plus试用(plus_eligible) / Free(free) / 封号(banned) / 凭证失效(token_invalid) / 网络错误(error)
    - 代理池 round-robin 轮流分配（支持直连与代理池）
    - 多 worker 线程池并发执行，支持取消 / 停止
    - 进度与日志通过 queue 广播，app.py 通过 SSE 实时推送到前端
    - 每个账号独立记录详细日志（带请求时间、状态码、响应体摘要）
    - 检测结果实时持久化到 SQLite registered 表的 extra_json.plus_check 中
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
from typing import Any, Optional
from urllib.parse import quote

try:
    from curl_cffi.requests import Session as CurlSession
except ImportError:  # pragma: no cover
    CurlSession = None

try:
    from . import db
    from .proxy_util import COUNTRY_LANG_MAP, new_proxy_session_id, resolve_target_country, route_proxy_country
except ImportError:
    import db
    from proxy_util import COUNTRY_LANG_MAP, new_proxy_session_id, resolve_target_country, route_proxy_country

CHECK_URL = "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
)

_DEACTIVATED_MARKERS = (
    "account_deactivated",
    "user_deactivated",
    "deactivated",
    "your account has been disabled",
    "account is disabled",
    "account has been blocked",
    "user is banned",
    "has been deleted or deactivated",
)


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _get_auth(claims: dict) -> dict:
    if not isinstance(claims, dict):
        return {}
    for k, v in claims.items():
        if "auth0.com" in k and isinstance(v, dict):
            return v
        if k in ("user_metadata", "app_metadata", "https://api.openai.com/auth") and isinstance(v, dict):
            return v
    return claims


def get_country_timezone_offset_min(country: str, tz_name: str = "") -> int:
    """获取指定国家对应的时区偏移量（以分钟为单位，与 JavaScript getTimezoneOffset 保持一致）。"""
    cc = (country or "").strip().upper()
    offsets = {
        "JP": -540,   # UTC+9 (日本)
        "BR": 180,    # UTC-3 (巴西圣保罗)
        "VN": -420,   # UTC+7 (越南)
        "AR": 180,    # UTC-3 (阿根廷)
        "ES": -60,    # UTC+1 (西班牙)
        "PL": -60,    # UTC+1 (波兰)
        "DE": -60,    # UTC+1 (德国)
        "GB": 0,      # UTC+0 (英国)
        "US": 300,    # UTC-5 (美东)
        "KR": -540,   # UTC+9 (韩国)
        "SG": -480,   # UTC+8 (新加坡)
        "TW": -480,   # UTC+8 (中国台湾)
        "HK": -480,   # UTC+8 (中国香港)
        "CN": -480,   # UTC+8 (中国大陆)
    }
    if cc in offsets:
        return offsets[cc]
    if tz_name:
        try:
            import zoneinfo
            from datetime import datetime
            tz = zoneinfo.ZoneInfo(tz_name)
            now = datetime.now(tz)
            offset = now.utcoffset()
            if offset is not None:
                return int(-offset.total_seconds() / 60)
        except Exception:
            pass
    return -540 if cc == "JP" else -480


def parse_account_plan(data: dict, body: str = "") -> dict:
    """深度解析 OpenAI accounts/check 返回的账号计划、Pro 倍率 (Pro 20x / Pro 5x / Team / Plus / Free / 试用)。"""
    accts = data.get("accounts", {})
    if not accts:
        return {"status": "error", "label": "无账户数据", "error": "响应 JSON 缺少 accounts 字段"}

    info = next(iter(accts.values()))
    acct = info.get("account", {})
    ent = info.get("entitlement", {})
    promo = info.get("eligible_promo_campaigns") or {}
    if not isinstance(promo, dict):
        promo = {}
    offers = info.get("eligible_offers") or []
    if not isinstance(offers, list):
        offers = []

    plan = str(acct.get("plan_type") or "free").lower().strip()
    structure = str(acct.get("structure") or "").lower().strip()
    sub_plan = str(ent.get("subscription_plan") or "").lower().strip()
    has_sub = bool(ent.get("has_active_subscription", False))

    # 汇总所有 features
    features_list = []
    if isinstance(acct.get("features"), list):
        features_list.extend(acct["features"])
    if isinstance(ent.get("features"), list):
        features_list.extend(ent["features"])
    features_str = " ".join(str(f) for f in features_list).lower() + " " + body.lower()

    if acct.get("is_deactivated", False):
        return {"status": "banned", "label": "封号", "plan": plan, "error": "is_deactivated=True"}

    # 1. 判定 ChatGPT Pro 计划及 20x / 5x 倍率
    is_pro = (
        plan == "pro"
        or "proplan" in sub_plan
        or "chatgpt_pro" in features_str
        or "o1_pro" in features_str
    )
    if is_pro:
        # 判定具体倍率 (20x vs 5x)
        if any(x in features_str for x in ("20x", "limit_20x", "limits_20x", "model_limits_20x", "rate_limit_20x")):
            return {
                "status": "pro_20x",
                "label": "👑 Pro 20x",
                "plan": "pro",
                "tier": "20x",
                "has_sub": has_sub,
                "note": "ChatGPT Pro 顶配 20 倍算力配额",
            }
        if any(x in features_str for x in ("5x", "limit_5x", "limits_5x", "model_limits_5x", "rate_limit_5x")):
            return {
                "status": "pro_5x",
                "label": "👑 Pro 5x",
                "plan": "pro",
                "tier": "5x",
                "has_sub": has_sub,
                "note": "ChatGPT Pro 5 倍算力配额",
            }
        return {
            "status": "pro_active",
            "label": "👑 Pro",
            "plan": "pro",
            "has_sub": has_sub,
            "note": "ChatGPT Pro 订阅生效中",
        }

    # 2. 判定 Team 团队版
    is_team = plan == "team" or "teamplan" in sub_plan or structure == "workspace"
    if is_team:
        return {
            "status": "team_active",
            "label": "💎 Team",
            "plan": "team",
            "has_sub": has_sub,
        }

    # 3. 判定可领试用活动 (仅以 eligible_promo_campaigns 中的真实活动为准)
    has_pro_promo = (
        "pro" in promo
        or any("pro" in str(k).lower() or "pro" in str(v.get("id", "")).lower() for k, v in promo.items() if isinstance(v, dict))
    )
    if has_pro_promo:
        return {
            "status": "pro_eligible",
            "label": "👑 可领Pro试用",
            "plan": plan,
            "promo": "pro-trial",
        }

    plus_promo_data = promo.get("plus") or promo.get("chatgpt_plus") or promo.get("chatgptplus")
    has_plus_promo = False
    plus_promo_id = ""

    if isinstance(plus_promo_data, dict):
        has_plus_promo = True
        plus_promo_id = str(plus_promo_data.get("id") or "plus-1-month-free")
    elif any("plus" in str(k).lower() or "trial" in str(k).lower() for k in promo.keys()):
        has_plus_promo = True
        plus_promo_id = "plus-trial"
    elif any("plus" in str(v.get("id", "")).lower() for v in promo.values() if isinstance(v, dict)):
        has_plus_promo = True
        plus_promo_id = "plus-trial"

    if has_plus_promo:
        return {
            "status": "plus_eligible",
            "label": "Plus试用",
            "plan": plan,
            "promo": plus_promo_id,
        }

    # 4. 判定 Plus 订阅生效
    if plan == "plus" or "plusplan" in sub_plan or has_sub:
        return {
            "status": "plus_active",
            "label": "Plus生效中",
            "plan": "plus",
            "has_sub": has_sub,
        }

    # 5. Free 普通账号
    return {
        "status": "free",
        "label": "Free",
        "plan": plan,
    }


def _looks_deactivated(body: str) -> bool:
    b_lower = body.lower()
    return any(m in b_lower for m in _DEACTIVATED_MARKERS)


def normalize_proxy(proxy: str) -> str:
    p = str(proxy or "").strip()
    if not p or p.startswith("#"):
        return ""
    if "://" in p:
        return p
    if "@" in p:
        return f"http://{p}"
    parts = p.split(":")
    if len(parts) == 4 and parts[1].isdigit() and "@" not in p:
        host, port, u, pwd = parts
        return f"http://{quote(u, safe='-._~')}:{quote(pwd, safe='-._~')}@{host}:{port}"
    if len(parts) == 3 and parts[2].isdigit() and ":" in parts[0]:
        credentials, host, port = p.rsplit(":", 2)
        u, pwd = credentials.split(":", 1)
        return f"http://{quote(u, safe='-._~')}:{quote(pwd, safe='-._~')}@{host}:{port}"
    return f"http://{p}"


def set_proxy(session: Any, proxy_url: str) -> None:
    if not proxy_url:
        return
    norm = normalize_proxy(proxy_url)
    if not norm:
        return
    if "socks5://" in norm:
        norm = norm.replace("socks5://", "socks5h://", 1)
    if hasattr(session, "proxies"):
        session.proxies = {"http": norm, "https": norm}


def safe_close(session: Any) -> None:
    if session is None:
        return
    try:
        session.close()
    except Exception:
        pass


class PlusCheckTask:
    """单个 Plus 状态检测任务。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config
        self.proxies: list[str] = config.get("proxies") or []
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        self.started_at = time.time()
        self.finished_at = 0.0

        # email -> item dict
        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "status": "pending",
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
            "pro_20x": 0,
            "pro_5x": 0,
            "pro_active": 0,
            "pro_eligible": 0,
            "team_active": 0,
            "plus_active": 0,
            "plus_eligible": 0,
            "free": 0,
            "banned": 0,
            "token_invalid": 0,
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
                if len(self.items[email]["logs"]) > 200:
                    self.items[email]["logs"] = self.items[email]["logs"][-200:]
        try:
            self.queue.put({"kind": "log", "email": email, "line": f"[{email}] {line}"})
        except Exception:
            pass

    def set_running(self, email: str) -> None:
        now = time.time()
        with self._lock:
            if email in self.items:
                self.items[email]["status"] = "running"
                self.items[email]["started_at"] = now
        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "running",
            "started_at": now,
        })

    def mark_done(self, email: str, result: dict) -> None:
        now = time.time()
        with self._lock:
            self.done_count += 1
            st = result.get("status") or "error"
            if st in self.stats:
                self.stats[st] += 1
            else:
                self.stats["error"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = "done"
                it["result"] = result
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "done",
            "result": result,
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_tasks: dict[str, PlusCheckTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 20


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-10]
        for k in oldest_keys:
            _tasks.pop(k, None)


def _check_one_account(task: PlusCheckTask, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被中止"})
        return

    task.set_running(email)
    task.add_email_log(email, f"开始检测 Plus 状态: {email}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "not_found", "label": "未找到", "error": "数据库中无此凭证"}
        task.add_email_log(email, "错误: 数据库中无此凭证记录")
        task.mark_done(email, res)
        return

    at = (cred.get("access_token") or "").strip()
    if not at:
        res = {"status": "no_at", "label": "无AT", "error": "该账号无 access_token"}
        task.add_email_log(email, "错误: 账号缺少 access_token，无法发起鉴权")
        task.mark_done(email, res)
        return

    auth_claims = _get_auth(_decode_jwt_payload(at))
    account_id = str(auth_claims.get("chatgpt_account_id") or auth_claims.get("account_id") or "").strip()
    device_id = (cred.get("device_id") or "").strip() or str(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"dango-check-plus:{email}")
    )

    proxy = task.next_proxy()
    raw_country = (task.config.get("proxy_country") or "").strip().upper()
    target_country = resolve_target_country(raw_country)
    if proxy and target_country:
        proxy = route_proxy_country(proxy, target_country, new_proxy_session_id())

    proxy_label = proxy.split("@")[-1] if "@" in proxy else (proxy or "直连")
    country_tip = f" (目标国家: {target_country})" if target_country else ""
    task.add_email_log(email, f"使用代理: {proxy_label}{country_tip}, account_id={account_id or '无'}, device_id={device_id[:8]}...")

    sess = None
    timeout = float(task.config.get("timeout") or 20.0)
    started_req = time.time()
    result = {"status": "error", "label": "网络异常", "error": "未知错误"}

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
                result = {"status": "banned", "label": "封号", "error": f"HTTP {status_code} 账号被禁用/封禁"}
                task.add_email_log(email, f"检测结论: 封号 (HTTP {status_code}) -> {body[:120]}")
            elif status_code == 401:
                result = {"status": "token_invalid", "label": "凭证失效", "error": "401 Unauthorized (Token失效/被吊销)"}
                task.add_email_log(email, f"检测结论: 凭证失效 (401) -> {body[:120]}")
            else:
                result = {"status": "error", "label": f"HTTP {status_code}", "error": f"HTTP {status_code}: {body[:120]}"}
                task.add_email_log(email, f"检测结论: HTTP {status_code} 访问受限 -> {body[:120]}")
        elif status_code == 200:
            try:
                data = resp.json() or {}
            except Exception:
                data = {}

            result = parse_account_plan(data, body)
            log_desc = f"检测结论: {result.get('label', result['status'])}"
            if result.get("note"):
                log_desc += f" ({result['note']})"
            task.add_email_log(email, log_desc)
        else:
            result = {"status": "error", "label": f"HTTP {status_code}", "error": f"服务器返回 HTTP {status_code}"}
            task.add_email_log(email, f"请求异常: HTTP {status_code} -> {body[:150]}")

    except Exception as e:
        req_ms = int((time.time() - started_req) * 1000)
        err_str = f"{type(e).__name__}: {e}"
        result = {"status": "error", "label": "网络异常", "error": err_str}
        task.add_email_log(email, f"请求失败 ({req_ms}ms): {err_str}")
    finally:
        safe_close(sess)

    # 写入数据库（有效状态写库更新）
    if result["status"] not in ("not_found", "no_at", "error", "cancelled"):
        db.update_plus_check(email, {**result, "checked_at": time.time()})

    task.mark_done(email, result)


def start(emails: list[str], config: dict) -> str:
    """启动 Plus 状态检测任务，返回 task_id。"""
    cleaned_emails = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned_emails:
        raise ValueError("请提供要检测的账号邮箱列表")

    task_id = f"plus_{uuid.uuid4().hex[:10]}"
    task = PlusCheckTask(task_id, cleaned_emails, config)
    workers = max(1, min(20, int(config.get("workers") or 5)))

    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    def _runner():
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"plus_{task_id}") as pool:
            futures = [pool.submit(_check_one_account, task, em) for em in cleaned_emails]
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass
        task.finished_at = time.time()
        try:
            task.queue.put({"kind": "end"})
        except Exception:
            pass

    t = threading.Thread(target=_runner, daemon=True, name=f"task_{task_id}")
    t.start()
    return task_id


def stop(task_id: str) -> bool:
    """停止指定的 Plus 检测任务。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return False
    task.cancelled = True
    for em, it in task.items.items():
        if it["status"] == "pending":
            task.mark_done(em, {"status": "cancelled", "label": "已取消", "error": "用户手动停止任务"})
    try:
        task.queue.put({"kind": "end"})
    except Exception:
        pass
    return True


def get_task(task_id: str) -> Optional[PlusCheckTask]:
    with _tasks_lock:
        return _tasks.get(task_id)


def get_queue(task_id: str) -> Optional[queue.Queue]:
    with _tasks_lock:
        task = _tasks.get(task_id)
        return task.queue if task else None


def snapshot(task_id: str) -> Optional[dict]:
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        return None
    with task._lock:
        return {
            "task_id": task.task_id,
            "cancelled": task.cancelled,
            "total": len(task.items),
            "done": task.done_count,
            "stats": dict(task.stats),
            "started_at": task.started_at,
            "finished_at": task.finished_at,
            "items": {k: dict(v) for k, v in task.items.items()},
        }
