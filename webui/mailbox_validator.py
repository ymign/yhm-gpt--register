"""webui/mailbox_validator.py — 邮箱号池快速验活与死号标记服务
================================================================
针对 Outlook / Hotmail / iCloud 等接码邮箱进行极速并发授权与连通性检测：
1. Outlook: 调 Microsoft OAuth Token Endpoint 验证 refresh_token 有效性（检测 AADSTS70000 等过期状态）
2. iCloud / 中转: 探测 relay_url 取码接口存活
3. 检测结果实时落库：失效死号自动标记为 failed 并记录失败原因，或直接从号池中剔除
4. 支持多 Worker 线程池高并发执行、实时 SSE 进度推送与中止
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

try:
    from . import db
    from mail_providers.outlook import get_outlook_access_token, FatalOutlookMailError
except ImportError:
    import db
    from mail_providers.outlook import get_outlook_access_token, FatalOutlookMailError

logger = logging.getLogger("mailbox_validator")


def validate_single_mailbox(account: dict, timeout: int = 15) -> dict:
    """快速检测单个邮箱账号凭证是否有效。

    Returns:
        dict: {"email": str, "valid": bool, "status": "available"|"failed", "reason": str}
    """
    email = (account.get("email") or "").strip().lower()
    kind = (account.get("kind") or "outlook").strip().lower()
    client_id = (account.get("client_id") or "").strip()
    refresh_token = (account.get("refresh_token") or "").strip()
    relay_url = (account.get("relay_url") or "").strip()

    if not email:
        return {"email": "", "valid": False, "status": "failed", "reason": "邮箱为空"}

    # 1. Outlook / Hotmail / Live 微软系邮箱
    if kind == "outlook" or any(dom in email for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
        if not refresh_token or not client_id:
            # 只有密码没有 refresh_token，微软现已全面禁用普通密码登录
            return {
                "email": email,
                "valid": False,
                "status": "failed",
                "reason": "缺少 refresh_token (微软已禁用普通密码直登)",
            }
        try:
            token_data = get_outlook_access_token(refresh_token, client_id)
            if token_data.get("access_token"):
                return {
                    "email": email,
                    "valid": True,
                    "status": "available",
                    "reason": "OAuth授权有效，收信正常",
                }
            return {
                "email": email,
                "valid": False,
                "status": "failed",
                "reason": "响应缺少 access_token",
            }
        except FatalOutlookMailError as e:
            err_text = str(e)
            if "AADSTS70000" in err_text or "invalid_grant" in err_text:
                reason = "OAuth授权已过期/被吊销 (AADSTS70000)"
            elif "AADSTS50076" in err_text or "AADSTS50079" in err_text:
                reason = "需强开二次验证 (AADSTS50076)"
            elif "AADSTS50034" in err_text:
                reason = "微软账号不存在 (AADSTS50034)"
            elif "AADSTS50057" in err_text:
                reason = "微软账号已被禁用 (AADSTS50057)"
            else:
                reason = f"微软OAuth失败: {err_text[:120]}"
            return {
                "email": email,
                "valid": False,
                "status": "failed",
                "reason": reason,
            }
        except Exception as e:
            return {
                "email": email,
                "valid": False,
                "status": "failed",
                "reason": f"网络或服务异常: {type(e).__name__}: {str(e)[:80]}",
            }

    # 2. iCloud 中转邮箱
    if kind == "icloud_relay" or relay_url:
        if not relay_url:
            return {"email": email, "valid": False, "status": "failed", "reason": "缺少 relay_url"}
        try:
            import urllib.request
            req = urllib.request.Request(relay_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if resp.status in (200, 204, 404):
                    return {"email": email, "valid": True, "status": "available", "reason": "中转接口连通正常"}
                return {"email": email, "valid": False, "status": "failed", "reason": f"中转接口 HTTP {resp.status}"}
        except Exception as e:
            return {"email": email, "valid": False, "status": "failed", "reason": f"中转接口请求失败: {e}"}

    # 3. 其它默认当有效
    return {"email": email, "valid": True, "status": "available", "reason": "正常"}


class MailboxValidatorTask:
    """邮箱号池批量验活任务管理器。"""

    def __init__(self, task_id: str, accounts: list[dict], config: dict):
        self.task_id = task_id
        self.accounts = accounts
        self.config = config
        self.action = config.get("action", "mark_failed")  # mark_failed 或 delete
        self.workers = min(30, max(1, int(config.get("workers") or 15)))
        self.started_at = time.time()
        self.finished_at = 0.0

        self.items: dict[str, dict] = {
            a["email"]: {
                "email": a["email"],
                "status": "pending",
                "valid": None,
                "reason": "",
                "elapsed": 0.0,
                "logs": [],
            }
            for a in accounts
        }
        self.queue: queue.Queue = queue.Queue()
        self.cancelled = False
        self.done_count = 0
        self.stats = {"valid": 0, "invalid": 0, "total": len(accounts)}
        self._lock = threading.Lock()

    def add_log(self, line: str, email: str = ""):
        ts = time.strftime("%H:%M:%S")
        formatted = f"{ts} {line}"
        try:
            self.queue.put({"kind": "log", "email": email, "line": formatted})
        except Exception:
            pass

    def mark_one_done(self, email: str, res: dict):
        now = time.time()
        with self._lock:
            self.done_count += 1
            if res.get("valid"):
                self.stats["valid"] += 1
            else:
                self.stats["invalid"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = res.get("status") or ("available" if res.get("valid") else "failed")
                it["valid"] = res.get("valid")
                it["reason"] = res.get("reason", "")
                it["elapsed"] = round(now - self.started_at, 2)

        # 数据库状态回写
        if not res.get("valid"):
            if self.action == "delete":
                db.delete_account(email)
            else:
                db.mark_failed(email, res.get("reason", "验活未通过"))
        else:
            # 有效且之前是 failed 的，重置为 available
            account_curr = db.get_account(email)
            if account_curr and account_curr.get("status") == "failed":
                db.reset_account(email)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "valid": res.get("valid"),
            "status": res.get("status"),
            "reason": res.get("reason"),
            "stats": dict(self.stats),
            "done_count": self.done_count,
        })


_tasks: dict[str, MailboxValidatorTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 15


def _worker_loop(task: MailboxValidatorTask):
    task.add_log(f"🚀 开始并发检测 {len(task.accounts)} 个邮箱账号 (并发: {task.workers} Worker, 动作: {task.action})...")

    def _check_wrapper(acct: dict):
        if task.cancelled:
            return
        em = acct.get("email", "")
        t0 = time.time()
        res = validate_single_mailbox(acct)
        cost = round(time.time() - t0, 2)
        if res.get("valid"):
            task.add_log(f"✅ [{em}] 有效 (耗时 {cost}s)", email=em)
        else:
            task.add_log(f"❌ [{em}] 失效: {res.get('reason')} (耗时 {cost}s)", email=em)
        task.mark_one_done(em, res)

    with ThreadPoolExecutor(max_workers=task.workers) as executor:
        futures = [executor.submit(_check_wrapper, a) for a in task.accounts]
        for f in futures:
            if task.cancelled:
                break
            try:
                f.result()
            except Exception as e:
                logger.warning(f"验活单个任务异常: {e}")

    task.finished_at = time.time()
    elapsed = round(task.finished_at - task.started_at, 1)
    task.add_log(
        f"🎉 全部验活完成！总数 {task.stats['total']}, 有效 {task.stats['valid']}, 失效 {task.stats['invalid']} (耗时 {elapsed}s)"
    )
    task.queue.put({"kind": "end", "stats": dict(task.stats), "elapsed": elapsed})


def start_mailbox_validation(emails: Optional[list[str]] = None, config: Optional[dict] = None) -> str:
    """启动号池批量验活任务。"""
    config = config or {}
    status_filter = config.get("status_filter", "")
    kind_filter = config.get("kind_filter", "")

    con = db._conn()
    if emails and len(emails) > 0:
        cleaned_emails = [e.strip().lower() for e in emails if e and e.strip()]
        placeholders = ",".join("?" for _ in cleaned_emails)
        cur = con.execute(f"SELECT * FROM outlook_accounts WHERE lower(email) IN ({placeholders})", cleaned_emails)
    else:
        sql = "SELECT * FROM outlook_accounts"
        where, args = [], []
        if status_filter:
            where.append("status=?")
            args.append(status_filter)
        if kind_filter:
            where.append("kind=?")
            args.append(kind_filter)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY imported_at ASC"
        cur = con.execute(sql, args)

    accounts = [dict(r) for r in cur.fetchall()]
    if not accounts:
        raise ValueError("没有符合条件的邮箱账号待检测")

    task_id = f"mbx_val_{uuid.uuid4().hex[:8]}"
    task = MailboxValidatorTask(task_id, accounts, config)

    with _tasks_lock:
        _tasks[task_id] = task
        if len(_tasks) > _MAX_HISTORY_TASKS:
            old_keys = list(_tasks.keys())[:-8]
            for k in old_keys:
                _tasks.pop(k, None)

    t = threading.Thread(target=_worker_loop, args=(task,), daemon=True)
    t.start()
    return task_id


def get_task(task_id: str) -> Optional[MailboxValidatorTask]:
    with _tasks_lock:
        return _tasks.get(task_id)


def stop_task(task_id: str) -> bool:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task:
            task.cancelled = True
            task.add_log("⚠️ 任务已被用户手动停止")
            return True
        return False
