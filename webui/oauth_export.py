"""重跑 OAuth 导出与凭证生成任务管理器 (Codex OAuth / CPA / Sub2API)。

核心功能：
  1. 对指定账号执行 Codex OAuth 直连重登与 Token 刷新（获取 access_token + refresh_token + id_token）
  2. 遇到手机验证（add-phone）直接标记失败/跳过，绝不阻塞接码或扣费
  3. 支持邮箱 OTP 自动取码推进登录态
  4. 成功后回写 access_token、refresh_token 到 SQLite 数据库
  5. 自动生成并持久化 CPA 格式 JSON 与 Sub2API 标准格式 JSON，支持一键打包与单独下载
  6. 多 Worker 线程池并发执行，支持取消 / 停止，SSE 实时广播进度与细分日志
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
from pathlib import Path
from typing import Any, Optional

from config import Config
from auth_flow import AuthFlow
from mail_providers import create_mail_provider, get_provider_class
from . import db
from .proxy_util import COUNTRY_LANG_MAP, new_proxy_session_id, resolve_target_country, route_proxy_country

logger = logging.getLogger(__name__)

EXPORTS_DIR = Path(__file__).resolve().parent / "exports"
CPA_DIR = EXPORTS_DIR / "cpa"
SUB2_DIR = EXPORTS_DIR / "sub2api"
CPA_DIR.mkdir(parents=True, exist_ok=True)
SUB2_DIR.mkdir(parents=True, exist_ok=True)


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return {}
        padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _get_account_claims(access_token: str) -> dict:
    payload = _decode_jwt_payload(access_token)
    auth = payload.get("https://api.openai.com/auth") or {}
    profile = payload.get("https://api.openai.com/profile") or {}
    exp = payload.get("exp")
    exp_iso = None
    if isinstance(exp, (int, float)):
        exp_iso = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "email": profile.get("email") or "",
        "name": profile.get("name") or "",
        "user_id": auth.get("chatgpt_user_id") or auth.get("user_id") or "",
        "account_id": auth.get("chatgpt_account_id") or "",
        "plan_type": auth.get("chatgpt_plan_type") or "",
        "exp": exp,
        "exp_iso": exp_iso,
    }


def cpa_credential_to_sub2_account(cpa: dict) -> dict:
    """将单个 CPA/Codex 凭证转换为 Sub2API accounts[] 元素。"""
    email = str(cpa.get("email") or "").strip()
    access_token = str(cpa.get("access_token") or "").strip()
    refresh_token = str(cpa.get("refresh_token") or "").strip()
    id_token = str(cpa.get("id_token") or "").strip()
    account_id = str(cpa.get("account_id") or cpa.get("chatgpt_account_id") or "").strip()
    plan_type = str(cpa.get("plan_type") or cpa.get("chatgpt_plan_type") or "").strip()
    exp_iso = str(cpa.get("expired") or cpa.get("expires_at") or "").strip()

    creds: dict[str, Any] = {"access_token": access_token}
    if refresh_token:
        creds["refresh_token"] = refresh_token
    if id_token:
        creds["id_token"] = id_token
    if email:
        creds["email"] = email
    if exp_iso:
        creds["expires_at"] = exp_iso
    if account_id:
        creds["chatgpt_account_id"] = account_id
    if plan_type:
        creds["plan_type"] = plan_type

    return {
        "name": email or "openai-oauth",
        "platform": "openai",
        "type": "oauth",
        "credentials": creds,
        "concurrency": 0,
        "priority": 0,
    }


def build_sub2api_payload(cpa_list: list[dict]) -> dict:
    """多条 CPA 凭证打包生成标准的 sub2api-data 导入格式。"""
    accounts = [cpa_credential_to_sub2_account(c) for c in cpa_list if c and c.get("access_token")]
    return {
        "type": "sub2api-data",
        "version": 1,
        "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "proxies": [],
        "accounts": accounts,
    }


class OAuthExportTask:
    """单个 OAuth 导出与凭证重跑任务。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config
        self.proxies: list[str] = config.get("proxies") or []
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        self.started_at = time.time()
        self.finished_at = 0.0

        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "status": "pending",
                "result": None,
                "started_at": 0.0,
                "finished_at": 0.0,
                "elapsed": 0.0,
                "logs": [],
                "cpa": None,
                "sub2api": None,
            }
            for e in emails
        }
        self.queue: queue.Queue = queue.Queue()
        self.cancelled = False
        self.done_count = 0
        self.stats = {
            "success": 0,
            "need_phone": 0,
            "error": 0,
            "token_invalid": 0,
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
                it["cpa"] = result.get("cpa")
                it["sub2api"] = result.get("sub2api")
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "done",
            "result": result,
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_tasks: dict[str, OAuthExportTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 20


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-10]
        for k in oldest_keys:
            _tasks.pop(k, None)


def _run_one_oauth_export(task: OAuthExportTask, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务被中止"})
        return

    task.set_running(email)
    task.add_email_log(email, f"开始重跑 OAuth 导出: {email}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "not_found", "label": "未找到", "error": "数据库中无此凭证记录"}
        task.add_email_log(email, "错误: 数据库中无此凭证记录")
        task.mark_done(email, res)
        return

    # 1. 代理路由
    proxy = task.next_proxy()
    raw_country = (task.config.get("proxy_country") or cred.get("reg_country") or "").strip().upper()
    target_country = resolve_target_country(raw_country)
    if proxy and target_country:
        proxy = route_proxy_country(proxy, target_country, new_proxy_session_id())

    proxy_label = proxy.split("@")[-1] if "@" in proxy else (proxy or "直连")
    country_tip = f" (目标国家: {target_country})" if target_country else ""
    task.add_email_log(email, f"使用网络出口: {proxy_label}{country_tip}")

    cfg = Config()
    cfg.proxy = proxy or None

    # 2. 邮箱取码准备（若需密码或 OTP）
    mail_source = db.get_setting("mail_source", "outlook")
    mail_account = db.get_account(email) or {"email": email}
    mail = create_mail_provider(mail_source, db.get_mail_settings(), mail_account)

    env_overrides = {
        "SKIP_SMS_ON_OAUTH": "1",                    # 强制跳过手机接码（遇到即失败）
        "OAUTH_CODEX_ADD_PHONE_REFRESH_RETRY": "0",  # 不反复尝试刷新 add-phone
        "OAUTH_CODEX_RT_ALLOW_RETRY": "1",
        "WEBUI_ALLOW_LOGIN": "1",
    }
    if target_country:
        env_overrides["TARGET_COUNTRY"] = target_country

    def _account_callback_for_flow(em: str) -> dict:
        d = db.get_registered(em) or {}
        return {
            "password": d.get("password", ""),
            "totp_secret": d.get("totp_secret", ""),
        }

    flow = AuthFlow(
        cfg,
        sms_callback=None,  # 禁用 SMS 接码控制器
        env_overrides=env_overrides,
        account_callback=_account_callback_for_flow,
    )
    flow.result.email = email

    # 尝试预置已有的 cookie header 和 device_id
    if cred.get("device_id"):
        flow.result.device_id = cred["device_id"]
    if cred.get("cookie_header"):
        try:
            for pair in cred["cookie_header"].split(";"):
                if "=" in pair:
                    k, v = pair.strip().split("=", 1)
                    flow.session.cookies.set(k.strip(), v.strip(), domain="chatgpt.com")
        except Exception:
            pass

    started_ts = time.time()
    try:
        task.add_email_log(email, "正在发起 Codex OAuth 鉴权换取 Refresh Token ...")
        ok = flow.oauth_codex_rt_exchange(mail_provider=mail)
        req_ms = int((time.time() - started_ts) * 1000)

        # 检查是否命中手机号验证
        final_url = getattr(flow, "_last_follow_url", "")
        if getattr(flow, "_need_phone_aborted", False) or "/add-phone" in str(final_url):
            res = {
                "status": "need_phone",
                "label": "需接码(已跳过)",
                "error": "OpenAI 要求绑定手机号 (已跳过)",
                "req_ms": req_ms,
            }
            db.update_registered_oauth_status(email, "need_phone", "需要手机号验证 (已跳过)")
            task.add_email_log(email, "检测到需要手机号验证 (已按要求跳过接码并标记失败)")
            task.mark_done(email, res)
            return

        if not ok or not flow.result.refresh_token:
            err_msg = "未能成功获取 refresh_token"
            res = {
                "status": "error",
                "label": "授权失败",
                "error": err_msg,
                "req_ms": req_ms,
            }
            db.update_registered_oauth_status(email, "failed", err_msg)
            task.add_email_log(email, f"OAuth 授权未完成 ({req_ms}ms): {err_msg}")
            task.mark_done(email, res)
            return

        # 授权成功：解析账号元数据
        at = flow.result.access_token or cred.get("access_token") or ""
        rt = flow.result.refresh_token or ""
        it = flow.result.id_token or cred.get("id_token") or ""
        claims = _get_account_claims(at) if at else {}
        account_id = claims.get("account_id") or ""
        plan_type = claims.get("plan_type") or "free"
        exp_iso = claims.get("exp_iso") or ""

        # 构造 CPA 格式 JSON
        cpa_data = {
            "type": "codex",
            "email": email,
            "access_token": at,
            "refresh_token": rt,
            "id_token": it,
            "account_id": account_id,
            "plan_type": plan_type,
            "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "expired": exp_iso,
        }

        # 构造 Sub2API 格式 JSON
        sub2_account = cpa_credential_to_sub2_account(cpa_data)

        # 落盘本地文件
        cpa_file = CPA_DIR / f"codex-{email}.json"
        sub2_file = SUB2_DIR / f"sub2-{email}.json"
        cpa_file.write_text(json.dumps(cpa_data, ensure_ascii=False, indent=2), encoding="utf-8")
        sub2_file.write_text(json.dumps(sub2_account, ensure_ascii=False, indent=2), encoding="utf-8")

        # 回写数据库 registered 表
        db.update_registered_oauth(
            email=email,
            access_token=at,
            refresh_token=rt,
            id_token=it,
            cookie_header=flow._build_chatgpt_cookie_header() or cred.get("cookie_header") or "",
            extra_data={
                "oauth_export": {
                    "exported_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "plan_type": plan_type,
                    "account_id": account_id,
                }
            },
        )

        res = {
            "status": "success",
            "label": "成功",
            "access_token_len": len(at),
            "refresh_token_len": len(rt),
            "id_token_len": len(it),
            "plan_type": plan_type,
            "account_id": account_id,
            "cpa": cpa_data,
            "sub2api": sub2_account,
            "req_ms": req_ms,
        }
        task.add_email_log(email, f"✅ OAuth 导出成功 ({req_ms}ms): RT={len(rt)} 字符, Plan={plan_type}, AccountId={account_id[:8]}...")
        task.mark_done(email, res)

    except Exception as e:
        req_ms = int((time.time() - started_ts) * 1000)
        err_str = str(e)
        if "add_phone" in err_str.lower() or "手机" in err_str:
            res = {"status": "need_phone", "label": "需接码(已跳过)", "error": "需要手机号验证 (已跳过)", "req_ms": req_ms}
            task.add_email_log(email, "检测到需要手机号验证 (已按要求跳过接码并标记失败)")
        else:
            res = {"status": "error", "label": "异常失败", "error": err_str, "req_ms": req_ms}
            task.add_email_log(email, f"OAuth 执行异常 ({req_ms}ms): {err_str}")
        task.mark_done(email, res)


def _worker_loop(task: OAuthExportTask, email_queue: queue.Queue) -> None:
    while not task.cancelled:
        try:
            email = email_queue.get_nowait()
        except queue.Empty:
            break
        try:
            _run_one_oauth_export(task, email)
        finally:
            email_queue.task_done()


def start(emails: list[str], config: dict) -> str:
    """启动 OAuth 导出任务。"""
    unique_emails = list(dict.fromkeys(e.strip().lower() for e in emails if e and e.strip()))
    if not unique_emails:
        raise ValueError("请提供至少一个要导出的账号邮箱")

    task_id = str(uuid.uuid4())[:12]
    task = OAuthExportTask(task_id, unique_emails, config)

    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    workers = max(1, min(20, int(config.get("workers") or 5)))
    email_queue: queue.Queue = queue.Queue()
    for em in unique_emails:
        email_queue.put(em)

    def _run():
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"oauth_export_{task_id}") as pool:
            futures = [pool.submit(_worker_loop, task, email_queue) for _ in range(workers)]
            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    logger.warning(f"[oauth_export] Worker 异常: {e}")

        task.finished_at = time.time()
        try:
            task.queue.put({"kind": "end", "task_id": task_id, "stats": task.stats})
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name=f"OAuthExportTaskRunner-{task_id}")
    t.start()
    return task_id


def stop(task_id: str) -> bool:
    """停止指定的任务。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task:
            task.cancelled = True
            try:
                task.queue.put({"kind": "end", "task_id": task_id, "cancelled": True})
            except Exception:
                pass
            return True
    return False


def snapshot(task_id: str) -> Optional[dict]:
    """获取任务当前的快照。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return None
        with task._lock:
            return {
                "task_id": task.task_id,
                "started_at": task.started_at,
                "finished_at": task.finished_at,
                "cancelled": task.cancelled,
                "done_count": task.done_count,
                "total": len(task.items),
                "stats": dict(task.stats),
                "items": {k: dict(v) for k, v in task.items.items()},
            }


def get_queue(task_id: str) -> Optional[queue.Queue]:
    with _tasks_lock:
        task = _tasks.get(task_id)
        return task.queue if task else None


def get_logs(task_id: str, email: str) -> list[str]:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return []
        with task._lock:
            it = task.items.get(email.lower().strip())
            return list(it["logs"]) if it else []


def export_cpa_bundle(task_id: str, emails: Optional[list[str]] = None) -> list[dict]:
    """获取指定任务中所有成功的 CPA 凭证列表。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
        if not task:
            return []
        with task._lock:
            cpa_list = []
            target_set = set(e.lower().strip() for e in emails) if emails else None
            for em, it in task.items.items():
                if target_set and em not in target_set:
                    continue
                if it.get("cpa"):
                    cpa_list.append(it["cpa"])
            return cpa_list


def export_sub2_bundle(task_id: str, emails: Optional[list[str]] = None) -> dict:
    """获取指定任务中所有成功账号的 Sub2API 聚合 JSON 数据。"""
    cpa_list = export_cpa_bundle(task_id, emails)
    return build_sub2api_payload(cpa_list)
