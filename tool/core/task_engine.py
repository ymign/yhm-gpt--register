"""task_engine.py — 批量高并发任务调度器与 SQLite 实时同步引擎
=================================================================
支持多线程并发、代理池轮换、任务暂停/恢复/终止、SQLite 实时落库与实时日志追踪。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from . import db
from .auth import AccountAuthWorker
from .proxy_util import new_proxy_session_id, route_proxy_for_worker

logger = logging.getLogger("engine")


def parse_account_line(line: str) -> Optional[dict]:
    """智能解析单行账号文本。

    支持格式：
      - 邮箱----密码----2FA
      - 邮箱----密码----2FA----自定义新密码
      - 邮箱,密码,2FA
      - 邮箱 密码 2FA
      - 邮箱:密码:2FA
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#") or raw.startswith("//"):
        return None

    # 分隔符识别
    parts = []
    if "----" in raw:
        parts = [p.strip() for p in raw.split("----") if p.strip()]
    elif "\t" in raw:
        parts = [p.strip() for p in raw.split("\t") if p.strip()]
    elif "," in raw:
        parts = [p.strip() for p in raw.split(",") if p.strip()]
    elif ":" in raw and "@" in raw:
        m = re.match(r"^([^:]+):([^:]+)(?::(.*))?$", raw)
        if m:
            parts = [m.group(1).strip(), m.group(2).strip()]
            if m.group(3):
                parts.append(m.group(3).strip())
    else:
        parts = [p.strip() for p in raw.split() if p.strip()]

    if len(parts) < 2:
        return None

    email = parts[0]
    if "@" not in email:
        return None

    password = parts[1]
    totp_secret = parts[2] if len(parts) >= 3 else ""
    inline_new_pwd = parts[3] if len(parts) >= 4 else ""

    return {
        "raw": raw,
        "email": email,
        "password": password,
        "totp_secret": totp_secret,
        "inline_new_pwd": inline_new_pwd,
    }


class BatchTaskEngine:
    """批量任务并发引擎单例。"""

    def __init__(self):
        self._lock = threading.RLock()
        self.state = "idle"  # "idle" | "running" | "paused" | "stopped" | "finished"

        self.accounts: list[dict] = []
        self.results: list[dict] = []
        self.system_logs: list[dict] = []

        self.options: dict = {}
        self.proxies: list[str] = []

        self.total_count = 0
        self.done_count = 0
        self.success_count = 0
        self.fail_count = 0

        self.start_time: float = 0
        self.finish_time: float = 0

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._executor: Optional[ThreadPoolExecutor] = None
        self._main_thread: Optional[threading.Thread] = None

    def _add_system_log(self, message: str, level: str = "info"):
        entry = {
            "time": time.strftime("%H:%M:%S"),
            "email": "System",
            "message": message,
            "level": level,
        }
        with self._lock:
            self.system_logs.append(entry)
            if len(self.system_logs) > 300:
                self.system_logs.pop(0)

    def get_status(self) -> dict:
        with self._lock:
            now = time.time()
            elapsed = int((self.finish_time or now) - (self.start_time or now)) if self.start_time else 0
            return {
                "state": self.state,
                "total": self.total_count,
                "done": self.done_count,
                "success": self.success_count,
                "fail": self.fail_count,
                "elapsed_seconds": max(0, elapsed),
                "running_accounts": self.accounts,
                "recent_logs": self.system_logs[-40:],
            }

    def start_from_text(self, text: str, options: dict) -> dict:
        """从批量文本导入并立即启动任务（支持入库与执行）。"""
        with self._lock:
            if self.state in ("running", "paused"):
                raise RuntimeError("当前已有任务正在运行中，请等待完成或点击停止")

            lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
            parsed_list = []
            seen = set()
            for line in lines:
                item = parse_account_line(line)
                if item and item["email"] not in seen:
                    item["status"] = "pending"
                    item["step"] = "等待执行"
                    item["error"] = ""
                    parsed_list.append(item)
                    seen.add(item["email"])

            if not parsed_list:
                raise ValueError("未解析到有效的账号行（需包含邮箱与密码）")

            # 1. 立即持久化入库 SQLite
            inserted, updated = db.import_accounts(parsed_list)
            self._add_system_log(f"批量导入成功: 新增 {inserted} 个, 更新 {updated} 个账号到 SQLite 数据库")

            # 2. 启动执行
            return self._launch_worker_pool(parsed_list, options)

    def start_selected(self, emails: list[str], options: dict) -> dict:
        """从数据库已有账号中选取指定邮箱启动任务（例如单账号重试或批量授权导出）。"""
        with self._lock:
            if self.state in ("running", "paused"):
                raise RuntimeError("当前已有任务正在运行中，请等待完成或点击停止")

            clean_emails = [e.strip().lower() for e in emails if e.strip()]
            if not clean_emails:
                raise ValueError("请先选择需要执行操作的账号")

            target_accounts = []
            for em in clean_emails:
                row = db.get_account_by_email(em)
                if row:
                    target_accounts.append({
                        "email": row["email"],
                        "password": row["password"],
                        "totp_secret": row["totp_secret"],
                        "inline_new_pwd": row.get("new_password") or "",
                        "status": "pending",
                        "step": "等待执行",
                        "error": "",
                    })

            if not target_accounts:
                raise ValueError("所选账号在数据库中未找到")

            return self._launch_worker_pool(target_accounts, options)

    def _launch_worker_pool(self, target_accounts: list[dict], options: dict) -> dict:
        self.accounts = target_accounts
        self.results = []
        self.options = dict(options or {})

        # 保存代理池与配置到 SQLite
        raw_proxies = self.options.get("proxy_pool") or self.options.get("proxy") or ""
        self.proxies = [p.strip() for p in raw_proxies.splitlines() if p.strip() and not p.startswith("#")]
        if raw_proxies:
            db.set_setting("proxy_pool", raw_proxies)
        if "concurrency" in self.options:
            db.set_setting("concurrency", str(self.options["concurrency"]))
        if "timeout" in self.options:
            db.set_setting("timeout", str(self.options["timeout"]))

        self.total_count = len(target_accounts)
        self.done_count = 0
        self.success_count = 0
        self.fail_count = 0
        self.start_time = time.time()
        self.finish_time = 0

        self._stop_event.clear()
        self._pause_event.clear()
        self.state = "running"

        self._add_system_log(f"任务已启动，共调度 {self.total_count} 个账号")

        self._main_thread = threading.Thread(target=self._run_all, daemon=True)
        self._main_thread.start()

        return self.get_status()

    def pause(self):
        with self._lock:
            if self.state == "running":
                self._pause_event.set()
                self.state = "paused"
                self._add_system_log("任务已暂停")

    def resume(self):
        with self._lock:
            if self.state == "paused":
                self._pause_event.clear()
                self.state = "running"
                self._add_system_log("任务已恢复运行")

    def stop(self):
        with self._lock:
            if self.state in ("running", "paused"):
                self._stop_event.set()
                self._pause_event.clear()
                self.state = "stopped"
                self._add_system_log("已发出停止指令，正在等待当前运行中的账号退出...")

    def _run_all(self):
        concurrency = max(1, min(50, int(self.options.get("concurrency") or 5)))
        cooldown = max(0, float(self.options.get("cooldown") or 1.0))

        self._executor = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="AuthWorker")

        def _worker_wrapper(index: int, item: dict):
            if self._stop_event.is_set():
                with self._lock:
                    item["status"] = "skipped"
                    item["step"] = "已取消"
                db.update_account_fields(item["email"], {"status": "idle", "step": "已取消"})
                return

            while self._pause_event.is_set() and not self._stop_event.is_set():
                time.sleep(0.5)

            if self._stop_event.is_set():
                with self._lock:
                    item["status"] = "skipped"
                    item["step"] = "已取消"
                db.update_account_fields(item["email"], {"status": "idle", "step": "已取消"})
                return

            email = item["email"]
            with self._lock:
                item["status"] = "running"
                item["step"] = "正在初始化"

            db.update_account_fields(email, {"status": "running", "step": "正在初始化", "error": ""})

            # 轮询挑选代理并为每个账号注入独立动态 Session ID (一号一独立 IP，防止风控关联)
            proxy = ""
            if self.proxies:
                raw_proxy = self.proxies[index % len(self.proxies)]
                worker_sid = new_proxy_session_id(8)
                proxy = route_proxy_for_worker(raw_proxy, session_id=worker_sid)

            def _step_cb(step_name: str):
                with self._lock:
                    item["step"] = step_name
                db.update_account_fields(email, {"step": step_name})

            def _log_cb(msg: str):
                t_str = time.strftime("%H:%M:%S")
                with self._lock:
                    item.setdefault("logs", []).append({"time": t_str, "message": msg})
                db.append_account_log(email, msg)

            worker = AccountAuthWorker(
                email=email,
                password=item["password"],
                totp_secret=item.get("totp_secret", ""),
                new_password_mode=self.options.get("new_password_mode", "keep"),
                custom_password=self.options.get("custom_password", ""),
                password_prefix=self.options.get("password_prefix", "Gpt@"),
                proxy=proxy,
                timeout=int(self.options.get("timeout") or 35),
                log_cb=_log_cb,
                step_cb=_step_cb,
            )

            try:
                res = worker.process(inline_new_pwd=item.get("inline_new_pwd", ""))
                new_pw = res.get("new_password") or res.get("password") or item["password"]

                with self._lock:
                    item["status"] = "success"
                    item["step"] = "执行成功"
                    item["password"] = new_pw
                    item["new_password"] = new_pw
                    item["access_token"] = res.get("access_token")
                    item["refresh_token"] = res.get("refresh_token")
                    item["id_token"] = res.get("id_token")
                    item["plan_type"] = res.get("plan_type")
                    self.results.append(res)
                    self.done_count += 1
                    self.success_count += 1

                # 同步回写 SQLite 数据库
                db.update_account_fields(
                    email,
                    {
                        "status": "success",
                        "step": "执行成功",
                        "password": new_pw,
                        "new_password": new_pw,
                        "access_token": res.get("access_token", ""),
                        "refresh_token": res.get("refresh_token", ""),
                        "id_token": res.get("id_token", ""),
                        "plan_type": res.get("plan_type", "free"),
                        "account_id": res.get("account_id", ""),
                        "auth_time": int(time.time()),
                        "error": "",
                    },
                )
                self._add_system_log(f"[{email}] ✅ 账号授权与凭据就绪 (套餐: {res.get('plan_type')})", level="success")

            except Exception as e:
                err_msg = str(e)
                with self._lock:
                    item["status"] = "failed"
                    item["step"] = "失败"
                    item["error"] = err_msg
                    self.results.append({
                        "ok": False,
                        "email": email,
                        "password": item["password"],
                        "totp_secret": item.get("totp_secret", ""),
                        "error": err_msg,
                    })
                    self.done_count += 1
                    self.fail_count += 1

                # 同步失败状态到 SQLite
                db.update_account_fields(
                    email,
                    {
                        "status": "failed",
                        "step": "失败",
                        "error": err_msg,
                    },
                )
                self._add_system_log(f"[{email}] ❌ 处理失败: {err_msg}", level="error")

            if cooldown > 0:
                time.sleep(cooldown)

        futures = []
        for idx, item in enumerate(self.accounts):
            if self._stop_event.is_set():
                break
            futures.append(self._executor.submit(_worker_wrapper, idx, item))

        for f in futures:
            try:
                f.result()
            except Exception:
                pass

        with self._lock:
            if self.state == "running":
                self.state = "finished"
            self.finish_time = time.time()
            self._add_system_log(f"全部任务执行完毕！成功 {self.success_count} 个 / 失败 {self.fail_count} 个")


# 全局单例
ENGINE = BatchTaskEngine()
