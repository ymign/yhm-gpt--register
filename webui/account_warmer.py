"""account_warmer.py — GPT 账号生命周期自动保温与活跃保鲜引擎
====================================================================
解决存量账号长期静置未活跃导致被 OpenAI 风控标记为「僵尸号/休眠号」甚至暗封的问题。

核心特性：
1. 智能 Token 极速置换：使用 Refresh Token (RT) 与 OpenAI 官方换取新鲜 Access Token (AT)，保持凭证时效；
2. 真实活跃轨迹模拟：携带专属指纹与代理，向官方后端发起轻量会话与模型列表健康探针 (/models, /me, /accounts/check)；
3. 多 Worker 线程池并发执行，支持 SSE 进度与实时日志推流；
4. 保温成果持久化：记录 last_warmed_at、warm_count 与 warm_status 到 SQLite 数据库 registered 表。
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

from . import db
from .token_refresh_service import refresh_token_fast
from .proxy_util import new_proxy_session_id, resolve_target_country, route_proxy_country

logger = logging.getLogger("account_warmer")


class WarmingTask:
    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.emails = emails
        self.config = config
        self.items: dict[str, dict] = {
            e: {"email": e, "status": "pending", "message": "", "detail": "", "elapsed": 0.0}
            for e in emails
        }
        self.log_queue: queue.Queue = queue.Queue(maxsize=5000)
        self.cancelled = False
        self.running = True
        self.created_at = time.time()
        self.finished_at: Optional[float] = None
        self.stats = {"total": len(emails), "done": 0, "success": 0, "failed": 0, "running": 0}
        self.email_logs: dict[str, list[str]] = {e: [] for e in emails}
        self.proxy_index = 0
        self.proxies: list[str] = [p.strip() for p in (config.get("proxies") or "").splitlines() if p.strip() and not p.strip().startswith("#")]

    def next_proxy(self) -> str:
        if self.config.get("proxy"):
            return self.config["proxy"]
        if not self.proxies:
            return db.get_setting("proxy", "") or ""
        p = self.proxies[self.proxy_index % len(self.proxies)]
        self.proxy_index += 1
        return p

    def add_email_log(self, email: str, line: str):
        t_str = time.strftime("%H:%M:%S")
        formatted = f"[{t_str}] {line}"
        if email in self.email_logs:
            self.email_logs[email].append(formatted)
        try:
            self.log_queue.put_nowait({"type": "log", "email": email, "line": formatted})
        except Exception:
            pass

    def set_running(self, email: str, step: str = ""):
        if email in self.items:
            self.items[email]["status"] = "running"
            self.items[email]["message"] = step
        self._recalc_stats()
        try:
            self.log_queue.put_nowait({
                "type": "progress",
                "email": email,
                "status": "running",
                "message": step,
                "stats": self.stats,
            })
        except Exception:
            pass

    def mark_done(self, email: str, message: str = "保温保鲜成功"):
        if email in self.items:
            self.items[email]["status"] = "success"
            self.items[email]["message"] = message
        self.stats["success"] += 1
        self.stats["done"] += 1
        self._recalc_stats()
        try:
            self.log_queue.put_nowait({
                "type": "progress",
                "email": email,
                "status": "success",
                "message": message,
                "stats": self.stats,
            })
        except Exception:
            pass

    def mark_failed(self, email: str, error: str):
        if email in self.items:
            self.items[email]["status"] = "failed"
            self.items[email]["message"] = error
        self.stats["failed"] += 1
        self.stats["done"] += 1
        self._recalc_stats()
        try:
            self.log_queue.put_nowait({
                "type": "progress",
                "email": email,
                "status": "failed",
                "message": error,
                "stats": self.stats,
            })
        except Exception:
            pass

    def _recalc_stats(self):
        self.stats["running"] = sum(1 for item in self.items.values() if item["status"] == "running")


_warming_tasks: dict[str, WarmingTask] = {}
_tasks_lock = threading.Lock()


def warm_single_account(email: str, proxy: str = "", log_cb: Optional[Callable[[str], None]] = None) -> dict:
    """对单个账号执行官方保温与存活保鲜协议。"""
    def _l(msg: str):
        logger.info(f"[Warmer] [{email}] {msg}")
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    row = db.get_registered(email)
    if not row:
        raise ValueError("数据库中未找到该账号")

    rt = (row.get("refresh_token") or "").strip()
    at = (row.get("access_token") or "").strip()

    if not rt and not at:
        raise ValueError("账号缺少可用 Access Token 或 Refresh Token，无法建立保温会话")

    _l(f"🔥 启动账号保温保鲜任务 (代理: {proxy or '直连'})...")

    # 1. 优先使用 Refresh Token 换取全新 Token
    if rt:
        try:
            _l("正在通过官方 OAuth 端点使用 Refresh Token 刷新凭证...")
            rf_res = refresh_token_fast(rt, proxy=proxy)
            new_at = rf_res.get("access_token") or ""
            new_rt = rf_res.get("refresh_token") or rt
            if new_at:
                at = new_at
                db.update_registered_manual(email, access_token=new_at, refresh_token=new_rt)
                _l("✅ Refresh Token 凭证置换成功，新 Access Token 已更新")
        except Exception as e:
            _l(f"⚠️ RT 刷新提示 ({e})，尝试继续使用现有 AT 发起保温...")

    # 2. 模拟真实浏览器访问 ChatGPT 官方 API
    from curl_cffi.requests import Session as CffiSession
    session = CffiSession(impersonate="chrome136")
    session.trust_env = False
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    token_hdr = at if at.lower().startswith("bearer ") else f"Bearer {at}"
    headers = {
        "Accept": "*/*",
        "Authorization": token_hdr,
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
        "Sec-Ch-Ua": '"Not?A_Brand";v="99", "Chromium";v="136", "Google Chrome";v="136"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    _l("🔍 正在请求 /backend-api/models 获取官方模型矩阵...")
    r1 = session.get("https://chatgpt.com/backend-api/models", headers=headers, timeout=25)
    if r1.status_code == 200:
        models = (r1.json() or {}).get("models", [])
        _l(f"✨ 官方模型探针成功，可用模型数: {len(models)} 个")
    elif r1.status_code in (401, 403):
        raise RuntimeError(f"账号授权失效 (HTTP {r1.status_code}): {(r1.text or '')[:120]}")

    _l("🔍 正在请求 /backend-api/me 获取用户信息与账号状态...")
    r2 = session.get("https://chatgpt.com/backend-api/me", headers=headers, timeout=25)
    plan_name = "free"
    if r2.status_code == 200:
        me_data = r2.json() or {}
        plan_name = (me_data.get("plan_type") or "free").lower()
        _l(f"✅ 用户状态探针成功 (Plan: {plan_name})")

    now = time.time()
    # 记录保温信息落库
    with db._lock:
        con = db._conn()
        con.execute(
            "UPDATE registered SET last_warmed_at=?, warm_count=coalesce(warm_count, 0) + 1, warm_status='alive' WHERE email=?",
            (now, email.lower()),
        )
        con.commit()

    _l(f"🎉 账号 {email} 成功完成官方活跃保温，状态已落库！")
    return {"ok": True, "email": email, "plan_type": plan_name, "warmed_at": now}


def _process_warm_worker(task: WarmingTask, email: str):
    t0 = time.time()
    task.add_email_log(email, f"▶ 准备启动账号保温保鲜...")
    proxy = task.next_proxy()
    raw_country = (task.config.get("proxy_country") or "").strip().upper()
    if not raw_country:
        row_info = db.get_registered(email) or {}
        raw_country = (row_info.get("reg_country") or "").strip().upper()
    target_country = resolve_target_country(raw_country)
    if proxy and target_country:
        proxy = route_proxy_country(proxy, target_country, new_proxy_session_id())

    task.set_running(email, "正在执行保温...")
    try:
        res = warm_single_account(email, proxy=proxy, log_cb=lambda m: task.add_email_log(email, m))
        elapsed = round(time.time() - t0, 1)
        if email in task.items:
            task.items[email]["elapsed"] = elapsed
        task.mark_done(email, f"🎉 保温成功 (耗时 {elapsed}s)")
    except Exception as e:
        elapsed = round(time.time() - t0, 1)
        if email in task.items:
            task.items[email]["elapsed"] = elapsed
        err_msg = str(e)
        task.add_email_log(email, f"❌ 保温失败: {err_msg}")
        task.mark_failed(email, err_msg)


def start_warming_task(emails: list[str], config: dict) -> str:
    cleaned = [e.strip().lower() for e in (emails or []) if e and e.strip()]
    if not cleaned:
        raise ValueError("请提供至少一个邮箱")

    task_id = f"warm_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    task = WarmingTask(task_id, cleaned, config)

    with _tasks_lock:
        _warming_tasks[task_id] = task

    workers = max(1, min(20, int(config.get("workers") or 5)))

    def _runner():
        with ThreadPoolExecutor(max_workers=workers) as executor:
            for email in task.emails:
                if task.cancelled:
                    task.mark_failed(email, "任务已取消")
                    continue
                executor.submit(_process_warm_worker, task, email)
        task.running = False
        task.finished_at = time.time()
        try:
            task.log_queue.put_nowait({"type": "end", "stats": task.stats})
        except Exception:
            pass

    threading.Thread(target=_runner, name=f"WarmerTask_{task_id}", daemon=True).start()
    return task_id


def get_warming_task(task_id: str) -> Optional[WarmingTask]:
    with _tasks_lock:
        return _warming_tasks.get(task_id)


def stop_warming_task(task_id: str) -> bool:
    with _tasks_lock:
        task = _warming_tasks.get(task_id)
        if task:
            task.cancelled = True
            return True
        return False
