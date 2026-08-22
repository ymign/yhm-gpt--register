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


def validate_single_mailbox(account: dict, timeout: int = 6, proxy: str = "") -> dict:
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
            token_data = get_outlook_access_token(refresh_token, client_id, timeout=timeout, proxy=proxy)
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
                "reason": f"网络超时或异常: {type(e).__name__}: {str(e)[:80]}",
            }

    # 2. iCloud 中转邮箱
    if kind == "icloud_relay" or relay_url:
        if not relay_url:
            return {"email": email, "valid": False, "status": "failed", "reason": "缺少 relay_url"}
        try:
            import requests
            proxies = {"http": proxy, "https": proxy} if proxy else None
            resp = requests.get(relay_url, headers={"User-Agent": "Mozilla/5.0"}, proxies=proxies, timeout=timeout)
            if resp.status_code in (200, 204, 404):
                return {"email": email, "valid": True, "status": "available", "reason": "中转接口连通正常"}
            return {"email": email, "valid": False, "status": "failed", "reason": f"中转接口 HTTP {resp.status_code}"}
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
        self.workers = min(50, max(1, int(config.get("workers") or 15)))
        self.started_at = time.time()
        self.finished_at = 0.0

        # 代理与动态住宅 IP 配置
        self.proxy = (config.get("proxy") or "").strip()
        self.proxy_pool = (config.get("proxy_pool") or "").strip()
        self.proxy_country = (config.get("proxy_country") or "").strip()

        self._proxy_list: list[str] = []
        if self.proxy_pool:
            self._proxy_list = [p.strip() for p in self.proxy_pool.splitlines() if p.strip() and not p.strip().startswith("#")]
        elif self.proxy:
            self._proxy_list = [self.proxy]

        self._proxy_idx = 0
        self._proxy_lock = threading.Lock()

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

    def get_proxy_for_account(self) -> str:
        """为每一个验活请求获取代理，若为动态住宅代理自动派生独立 session_id (一号一 IP)。"""
        if not self._proxy_list:
            return ""
        with self._proxy_lock:
            p = self._proxy_list[self._proxy_idx % len(self._proxy_list)]
            self._proxy_idx += 1

        try:
            try:
                from .proxy_util import route_proxy_country, new_proxy_session_id
            except ImportError:
                from proxy_util import route_proxy_country, new_proxy_session_id
            sid = new_proxy_session_id(8)
            routed = route_proxy_country(p, self.proxy_country, session_id=sid)
            return routed or p
        except Exception:
            return p

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

        # 1. 数据库状态高效回写（仅在需要变更时写入，避免对数千个正常号进行无谓的 SELECT 查库）
        if not res.get("valid"):
            if self.action == "delete":
                db.delete_account(email)
            else:
                db.mark_failed(email, res.get("reason", "验活未通过"))
        elif self.config.get("status_filter") == "failed":
            # 仅当在复检 failed 状态的列表且恢复成功时，重置为 available
            db.reset_account(email)

        # 2. SSE 事件推送控制（关键性能优化）
        # 失效死号：立即推送 failed_item 让前端死号清单表格更新
        if not res.get("valid"):
            self.queue.put({
                "kind": "failed_item",
                "email": email,
                "reason": res.get("reason", "授权失效"),
                "elapsed": round(now - self.started_at, 2),
                "stats": dict(self.stats),
                "done_count": self.done_count,
            })
        else:
            # 正常号：节流推送进度更新，每 10 个或完成时推送一次汇总状态，防止数万条消息轰炸卡死浏览器
            if self.done_count % 10 == 0 or self.done_count == self.stats["total"]:
                self.queue.put({
                    "kind": "progress",
                    "stats": dict(self.stats),
                    "done_count": self.done_count,
                })


_tasks: dict[str, MailboxValidatorTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 15


def _worker_loop(task: MailboxValidatorTask):
    task.add_log(f"🚀 开始并发检测 {len(task.accounts)} 个邮箱账号 (并发: {task.workers} Worker, 动作: {task.action})...")

    work_q: queue.Queue = queue.Queue()
    for a in task.accounts:
        work_q.put(a)

    def _worker():
        while not task.cancelled:
            try:
                acct = work_q.get_nowait()
            except queue.Empty:
                break
            try:
                em = acct.get("email", "")
                t0 = time.time()
                proxy_for_run = task.get_proxy_for_account()
                res = validate_single_mailbox(acct, timeout=6, proxy=proxy_for_run)
                cost = round(time.time() - t0, 2)
                if res.get("valid"):
                    task.add_log(f"✅ [{em}] 有效 (耗时 {cost}s)", email=em)
                else:
                    task.add_log(f"❌ [{em}] 失效: {res.get('reason')} (耗时 {cost}s)", email=em)
                task.mark_one_done(em, res)
            except Exception as e:
                logger.warning(f"验活单个任务异常: {e}")
            finally:
                work_q.task_done()

    threads = []
    for _ in range(task.workers):
        t = threading.Thread(target=_worker, daemon=True)
        threads.append(t)
        t.start()

    for t in threads:
        t.join()

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
