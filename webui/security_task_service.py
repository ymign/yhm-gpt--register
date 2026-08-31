"""security_task_service.py — 账号安全加固任务管理器 (批量官方补设密码 & 批量补绑 2FA)
=====================================================================================
提供并发多 Worker 任务流、SSE 实时推流、单账号独立实时日志与失败账号一键重试。
"""
from __future__ import annotations

import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

try:
    from . import db
    from .official_password import official_set_account_password
    from .two_factor import bind_totp_2fa_adaptive, generate_random_password
    from .proxy_util import new_proxy_session_id, resolve_target_country, route_proxy_country
except ImportError:
    import db
    from official_password import official_set_account_password
    from two_factor import bind_totp_2fa_adaptive, generate_random_password
    from proxy_util import new_proxy_session_id, resolve_target_country, route_proxy_country

logger = logging.getLogger(__name__)


class SecurityTask:
    """单个安全加固批量任务实例。"""

    def __init__(self, task_id: str, action: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.action = str(action or "password").strip().lower()  # password / 2fa
        self.config = config or {}
        self.proxies: list[str] = config.get("proxies") or []
        self._proxy_idx = 0
        self._idx_lock = threading.Lock()
        self.started_at = time.time()
        self.finished_at = 0.0

        # email -> item dict
        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "action": self.action,
                "status": "pending",
                "step_text": "排队中...",
                "result": None,
                "error": "",
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
            "total": len(emails),
            "done": 0,
            "success": 0,
            "fail": 0,
            "skipped": 0,
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

    def set_running(self, email: str, step_text: str = "正在执行...") -> None:
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
            self.stats["done"] = self.done_count
            self.stats["success"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = "success"
                it["result"] = result
                it["step_text"] = result.get("label") or "✅ 处理完成"
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "success",
            "step_text": result.get("label") or "✅ 处理完成",
            "result": result,
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })

    def mark_failed(self, email: str, error_msg: str) -> None:
        now = time.time()
        with self._lock:
            self.done_count += 1
            self.stats["done"] = self.done_count
            self.stats["fail"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = "failed"
                it["error"] = error_msg
                it["step_text"] = f"❌ 失败: {error_msg}"
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "failed",
            "step_text": f"❌ 失败: {error_msg}",
            "error": error_msg,
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })

    def mark_skipped(self, email: str, reason: str) -> None:
        now = time.time()
        with self._lock:
            self.done_count += 1
            self.stats["done"] = self.done_count
            self.stats["skipped"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = "skipped"
                it["step_text"] = f"⚪ 已跳过: {reason}"
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": "skipped",
            "step_text": f"⚪ 已跳过: {reason}",
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_tasks: dict[str, SecurityTask] = {}
_tasks_lock = threading.Lock()
_MAX_HISTORY_TASKS = 20


def _prune_tasks_locked() -> None:
    if len(_tasks) > _MAX_HISTORY_TASKS:
        oldest_keys = list(_tasks.keys())[:-10]
        for k in oldest_keys:
            _tasks.pop(k, None)


def _process_password_item(task: SecurityTask, email: str, proxy: str, timeout: int, official_reset: bool) -> None:
    """处理单个账号的补设密码。"""
    task.add_email_log(email, f"▶ 启动补设密码任务 (官方全自动生效={official_reset}, 代理={proxy or '直连'})")

    def _step(s: str):
        task.set_running(email, s)

    def _log(l: str):
        task.add_email_log(email, l)

    row = db.get_registered(email) or {}
    new_pw = generate_random_password(16)

    if not official_reset:
        _step("正在写入本地数据库...")
        _log(f"已生成随机密码: {new_pw}，正在回写本地数据库...")
        db.update_registered_manual(email, password=new_pw)
        task.mark_done(email, {
            "password": new_pw,
            "official_applied": False,
            "label": "✅ 本地密码已保存",
        })
        _log("✅ 本地密码更新成功")
        return

    try:
        _step("正在启动 OpenAI 官方重置密码认证流...")
        res = official_set_account_password(
            email=email,
            new_password=new_pw,
            proxy=proxy,
            timeout=timeout,
            step_cb=_step,
            log_cb=_log,
        )
        task.mark_done(email, {
            "password": res.get("password") or new_pw,
            "official_applied": bool(res.get("official_applied", False)),
            "is_passwordless": bool(res.get("is_passwordless", False)),
            "label": res.get("label") or ("🎉 官方服务端设密成功" if res.get("official_applied") else "✅ 官方免密已验活"),
        })
        _log(res.get("message") or f"🎉 设密任务完成：密码 {res.get('password') or new_pw} 已更新！")
    except Exception as e:
        err_str = str(e)
        _log(f"❌ 官方设密失败: {err_str}")
        task.mark_failed(email, err_str)


def _process_2fa_item(task: SecurityTask, email: str, proxy: str) -> None:
    """处理单个账号的补绑 2FA。"""
    task.add_email_log(email, f"▶ 启动补绑 2FA 任务 (代理={proxy or '直连'})")

    def _step(s: str):
        task.set_running(email, s)

    def _log(l: str):
        task.add_email_log(email, l)

    row = db.get_registered(email)
    if not row:
        task.mark_failed(email, "数据库中未找到该账号")
        return

    from .two_factor import totp_now

    if (row.get("totp_secret") or "").strip():
        current_sec = row["totp_secret"].strip()
        code_now = totp_now(current_sec)
        _log(f"ℹ️ 该账号本地已登记有 2FA Secret: {current_sec} (当前动态验证码: {code_now})，无需重复绑定")
        task.mark_done(email, {
            "totp_secret": current_sec,
            "already_bound": True,
            "label": "✅ 已绑定 2FA",
        })
        return

    try:
        _step("正在启动自适应 2FA 绑定...")
        res = bind_totp_2fa_adaptive(row=row, proxy=proxy, step_cb=_step, log_cb=_log)
        sec = res.get("secret") or res.get("totp_secret") or ""
        if res.get("already_bound"):
            if sec:
                code_now = totp_now(sec)
                task.mark_done(email, {
                    "totp_secret": sec,
                    "already_bound": True,
                    "label": "✅ 已绑定 2FA",
                })
                _log(f"✅ 官方服务端确认已开启 2FA！Secret: {sec} | 当前动态码: {code_now}")
            else:
                task.mark_done(email, {
                    "already_bound": True,
                    "label": "✅ 官方已开启 2FA",
                })
                _log("ℹ️ 官方服务端确认已开启 2FA TOTP（注：OpenAI 官方仅在首次生成时下发 Secret 明文）")
        else:
            code_now = totp_now(sec) if sec else ""
            task.mark_done(email, {
                "totp_secret": sec,
                "label": "🎉 2FA 绑定成功",
            })
            _log(f"🎉 2FA 官方激活成功！Secret: {sec} | 即时动态码: {code_now}，已落库保存")
    except Exception as e:
        err_str = str(e)
        _log(f"❌ 2FA 绑定失败: {err_str}")
        task.mark_failed(email, err_str)


def _worker_runner(task: SecurityTask, emails_to_run: list[str]) -> None:
    """线程池调度执行。"""
    workers = max(1, min(10, int(task.config.get("workers") or 3)))
    timeout = max(10, min(120, int(task.config.get("timeout") or 60)))
    official_reset = bool(task.config.get("official_reset", True))

    logger.info(f"[SecurityTask] 启动任务 task_id={task.task_id} action={task.action} 总数={len(emails_to_run)} workers={workers}")

    def _run_single(email: str):
        if task.cancelled:
            task.mark_skipped(email, "任务已取消")
            return
        proxy = task.next_proxy()
        raw_country = (task.config.get("proxy_country") or "").strip().upper()
        if not raw_country:
            row_info = db.get_registered(email) or {}
            raw_country = (row_info.get("reg_country") or "").strip().upper()
        target_country = resolve_target_country(raw_country)
        if proxy and target_country:
            proxy = route_proxy_country(proxy, target_country, new_proxy_session_id())

        if task.action == "password":
            _process_password_item(task, email, proxy, timeout, official_reset)
        else:
            _process_2fa_item(task, email, proxy)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_run_single, e) for e in emails_to_run]
        for f in futures:
            try:
                f.result()
            except Exception as e:
                logger.error(f"[SecurityTask] worker 执行未捕获异常: {e}")

    task.finished_at = time.time()
    task.queue.put({
        "kind": "done",
        "stats": task.stats,
        "finished_at": task.finished_at,
        "elapsed": round(task.finished_at - task.started_at, 1),
    })
    logger.info(f"[SecurityTask] 任务完成 task_id={task.task_id} stats={task.stats}")


def start_security_task(action: str, emails: list[str], config: dict) -> dict:
    """启动安全加固批量任务。"""
    cleaned_emails = [e.strip().lower() for e in emails if e and e.strip()]
    if not cleaned_emails:
        raise ValueError("目标账号列表不能为空")

    task_id = f"sec_{uuid.uuid4().hex[:12]}"
    task = SecurityTask(task_id=task_id, action=action, emails=cleaned_emails, config=config)

    with _tasks_lock:
        _prune_tasks_locked()
        _tasks[task_id] = task

    t = threading.Thread(target=_worker_runner, args=(task, cleaned_emails), daemon=True, name=f"sec-task-{task_id}")
    t.start()

    return {
        "ok": True,
        "task_id": task_id,
        "action": task.action,
        "total": len(cleaned_emails),
        "items": task.items,
    }


def retry_security_task(task_id: str, emails: Optional[list[str]] = None) -> dict:
    """一键重试失败或指定的账号。"""
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"未找到任务: {task_id}")

    with task._lock:
        if emails:
            target_emails = [e.strip().lower() for e in emails if e.strip().lower() in task.items]
        else:
            target_emails = [e for e, it in task.items.items() if it.get("status") == "failed"]

        if not target_emails:
            return {"ok": False, "message": "没有需要重试的失败账号"}

        for e in target_emails:
            it = task.items[e]
            old_status = it.get("status")
            if old_status in task.stats and task.stats[old_status] > 0:
                task.stats[old_status] -= 1
            it["status"] = "pending"
            it["step_text"] = "待重试..."
            it["error"] = ""
            task.stats["done"] = max(0, task.stats["done"] - 1)
            task.done_count = max(0, task.done_count - 1)

    t = threading.Thread(target=_worker_runner, args=(task, target_emails), daemon=True, name=f"sec-retry-{task_id}")
    t.start()

    return {
        "ok": True,
        "task_id": task_id,
        "retrying_count": len(target_emails),
        "emails": target_emails,
    }


def stop_security_task(task_id: str) -> dict:
    with _tasks_lock:
        task = _tasks.get(task_id)
    if not task:
        raise ValueError(f"未找到任务: {task_id}")
    task.cancelled = True
    return {"ok": True, "message": "已发送停止信号"}


def get_security_task(task_id: str) -> Optional[SecurityTask]:
    with _tasks_lock:
        return _tasks.get(task_id)
