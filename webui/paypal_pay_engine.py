"""PayPal 协议支付 (Agreement Approval) 执行与调度引擎。

提供 PayPal 协议自动代付/签约授权任务调度：
  1. 接收 ba_token (或 BA 链接) 及手机号
  2. 自动生成符合国家规范的买家、虚拟卡与地址资料
  3. 执行 PayPal 纯协议全流程 (ElevationFlow / PayPalFlow)
  4. 支持 2FA 短信验证码实时交互输入与换号重试
  5. 实时推送 Loguru 全量协议日志与任务进度 SSE
"""
from __future__ import annotations

import json
import logging
import queue
import re
import secrets
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger as loguru_logger

try:
    from . import db
    from .paypal_protocol.flow import PayPalFlow
    from .paypal_protocol.elevation_flow import IdentityElevationPayPalFlow
    from .paypal_protocol.models import (
        BillingAddress,
        CardInfo,
        UserInfo,
        generate_address,
        generate_card,
        generate_user,
    )
    from .paypal_protocol.proxy import ProxyConfig, ProxyEntry, build_proxy_config
    from .paypal_protocol.session import sanitize_for_log
    from .paypal_protocol.runtime_country_resolver import (
        infer_dynamic_kyc,
        resolve_runtime_country_schema,
        validate_runtime_address,
        validate_runtime_phone,
    )
except ImportError:
    import db
    from paypal_protocol.flow import PayPalFlow
    from paypal_protocol.elevation_flow import IdentityElevationPayPalFlow
    from paypal_protocol.models import (
        BillingAddress,
        CardInfo,
        UserInfo,
        generate_address,
        generate_card,
        generate_user,
    )
    from paypal_protocol.proxy import ProxyConfig, ProxyEntry, build_proxy_config
    from paypal_protocol.session import sanitize_for_log
    from paypal_protocol.runtime_country_resolver import (
        infer_dynamic_kyc,
        resolve_runtime_country_schema,
        validate_runtime_address,
        validate_runtime_phone,
    )

logger = logging.getLogger(__name__)


class WebPayPalFlowAdapter(PayPalFlow):
    """适配 Web 任务队列的 PayPalFlow：拦截交互式输入并上报状态与日志。"""

    def __init__(self, *args, task: PayPalPayJobTask, item_key: str, **kwargs):
        super().__init__(*args, **kwargs)
        self.task = task
        self.item_key = item_key

    def _prompt_operator(self, prompt: str) -> str:
        self.task.add_item_log(self.item_key, f"交互提示: {prompt}")
        return self.task.wait_for_input(self.item_key, prompt)


class WebIdentityElevationPayPalFlowAdapter(WebPayPalFlowAdapter, IdentityElevationPayPalFlow):
    """适配 Web 任务队列的 IdentityElevationPayPalFlow。"""
    pass


class PayPalPayJobTask:
    def __init__(
        self,
        task_id: str,
        items: list[dict],  # [{"email": "...", "ba_token": "BA-...", "phone": "+55..."}]
        workers: int = 2,
        country: str = "BR",
        flow_mode: str = "elevation",  # elevation / standard
        sms_provider_name: str = "",
        sms_api_key: str = "",
        sms_country: str = "52",
        proxy_pool: str = "",
    ):
        self.task_id = task_id
        self.raw_items = items
        self.workers = max(1, min(workers, 10))
        self.country = country.upper()
        self.flow_mode = flow_mode
        self.sms_provider_name = sms_provider_name
        self.sms_api_key = sms_api_key
        self.sms_country = sms_country
        self.proxy_pool = proxy_pool
        self.cancelled = False

        self.queue: queue.Queue = queue.Queue()
        self.logs: list[str] = []
        self.item_logs: dict[str, list[str]] = {}
        self.items: dict[str, dict[str, Any]] = {}
        self.input_queues: dict[str, list[str]] = {}
        self.conditions: dict[str, threading.Condition] = {}
        self._lock = threading.Lock()

        for it in items:
            key = it.get("email") or it.get("ba_token") or str(uuid.uuid4())
            self.items[key] = {
                "key": key,
                "email": it.get("email", ""),
                "ba_token": it.get("ba_token", ""),
                "phone": it.get("phone", ""),
                "status": "pending",
                "step_text": "排队中...",
                "prompt": "",
                "result": None,
                "started_at": 0,
                "elapsed": 0,
            }
            self.item_logs[key] = []
            self.input_queues[key] = []
            self.conditions[key] = threading.Condition()

    def add_log(self, msg: str):
        now_str = datetime.now().strftime("%H:%M:%S")
        line = f"{now_str} {msg}"
        with self._lock:
            self.logs.append(line)
            if len(self.logs) > 800:
                self.logs.pop(0)
        self.queue.put({"kind": "log", "line": line})

    def add_item_log(self, key: str, msg: str):
        now_str = datetime.now().strftime("%H:%M:%S")
        line = f"{now_str} {msg}"
        with self._lock:
            if key in self.item_logs:
                self.item_logs[key].append(line)
                if len(self.item_logs[key]) > 400:
                    self.item_logs[key].pop(0)
        self.queue.put({"kind": "log", "key": key, "line": f"[{key[:12]}] {msg}"})

    def set_running(self, key: str, step_text: str = "执行中..."):
        with self._lock:
            if key in self.items:
                self.items[key]["status"] = "running"
                self.items[key]["step_text"] = step_text
                self.items[key]["prompt"] = ""
                if not self.items[key]["started_at"]:
                    self.items[key]["started_at"] = time.time()
                self.queue.put({
                    "kind": "progress",
                    "key": key,
                    "email": self.items[key]["email"],
                    "status": "running",
                    "step_text": step_text,
                    "prompt": "",
                    "started_at": self.items[key]["started_at"],
                })

    def wait_for_input(self, key: str, prompt: str) -> str:
        """等待前端操作员输入验证码或新手机号。"""
        cond = self.conditions.get(key)
        if not cond:
            raise RuntimeError("Task condition missing")

        with cond:
            if self.cancelled:
                raise RuntimeError("任务已取消")
            with self._lock:
                if key in self.items:
                    self.items[key]["status"] = "awaiting_otp"
                    self.items[key]["step_text"] = "等待输入验证码/换手机号"
                    self.items[key]["prompt"] = prompt
            self.queue.put({
                "kind": "progress",
                "key": key,
                "email": self.items[key]["email"],
                "status": "awaiting_otp",
                "step_text": "等待输入验证码/换手机号",
                "prompt": prompt,
            })

            # 等待前端调用 submit_input
            deadline = time.time() + 600  # 10 分钟等待超时
            while not self.input_queues[key]:
                if self.cancelled:
                    raise RuntimeError("任务已取消")
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise TimeoutError("等待短信验证码超时")
                cond.wait(timeout=min(1.0, remaining))

            val = self.input_queues[key].pop(0).strip()
            self.set_running(key, "已收到验证码/手机号，正在继续授权...")
            return val

    def submit_input(self, key: str, value: str):
        """前端操作员提交 2FA 验证码或手机号。"""
        cond = self.conditions.get(key)
        if not cond:
            raise ValueError("Task not found")
        with cond:
            self.input_queues[key].append(value.strip())
            cond.notify_all()

    def mark_done(self, key: str, result: dict):
        with self._lock:
            if key in self.items:
                st = result.get("status", "done")
                self.items[key]["status"] = st
                self.items[key]["result"] = result
                self.items[key]["step_text"] = result.get("label") or ("支付授权成功" if st == "success" else "授权失败")
                self.items[key]["prompt"] = ""
                if self.items[key]["started_at"]:
                    self.items[key]["elapsed"] = int(time.time() - self.items[key]["started_at"])
                self.queue.put({
                    "kind": "progress",
                    "key": key,
                    "email": self.items[key]["email"],
                    "status": st,
                    "result": result,
                    "step_text": self.items[key]["step_text"],
                    "prompt": "",
                    "elapsed": self.items[key]["elapsed"],
                })


_active_pay_tasks: dict[str, PayPalPayJobTask] = {}
_pay_tasks_lock = threading.Lock()


def _get_proxy_for_run(pool_str: str) -> Optional[ProxyConfig]:
    lines = [line.strip() for line in (pool_str or "").splitlines() if line.strip() and not line.startswith("#")]
    if not lines:
        return build_proxy_config(enabled=False)
    p = secrets.choice(lines)
    return ProxyConfig(enabled=True, url=p, raw=p)


def _execute_single_pay(task: PayPalPayJobTask, item_key: str) -> None:
    if task.cancelled:
        task.mark_done(item_key, {"status": "cancelled", "label": "已停止", "error": "任务已停止"})
        return

    it = task.items[item_key]
    ba_token = it.get("ba_token", "")
    email = it.get("email", "")
    phone = it.get("phone", "")

    # 解析 ba_token
    if ba_token.startswith("http"):
        m = re.search(r"ba_token=([A-Za-z0-9-]+)", ba_token)
        if m:
            ba_token = m.group(1)

    if not ba_token:
        # 尝试从数据库根据 email 读取已提链的 ba_token
        if email:
            reg = db.get_registered(email)
            if reg and reg.get("extra"):
                ext = reg["extra"].get("extract_link") or {}
                ba_token = ext.get("ba_token") or ""
                if not ba_token and ext.get("link_url"):
                    m = re.search(r"ba_token=([A-Za-z0-9-]+)", ext["link_url"])
                    if m:
                        ba_token = m.group(1)

    if not ba_token:
        res = {"status": "error", "label": "缺少 BA Token", "error": "未提供 BA Token，也未能从账号提取记录中匹配"}
        task.add_item_log(item_key, "错误: 未提供有效的 BA Token")
        task.mark_done(item_key, res)
        return

    task.set_running(item_key, "初始化买家与虚拟卡资料...")
    task.add_item_log(item_key, f"开始处理 BA Token: {ba_token[:12]}***")

    proxy_cfg = _get_proxy_for_run(task.proxy_pool)
    country = task.country or "BR"

    # 将 Loguru 日志输出挂钩到任务日志
    sink_id = loguru_logger.add(
        lambda msg: task.add_item_log(item_key, str(msg).strip()),
        format="{message}",
        level="INFO",
    )

    try:
        # 自动生成符合国家规范的买家、卡片与地址
        default_phone = phone if phone else ("+66812345678" if country == "TH" else "+5591980133818")
        user = generate_user(default_phone, country=country)
        card = generate_card(proxy_url=proxy_cfg.url if proxy_cfg.enabled else None)
        address = generate_address(country=country)

        task.add_item_log(item_key, f"生成买家: {user.first_name} {user.last_name} ({user.phone}) | 卡: {card.number[:6]}***{card.number[-4:]}")
        task.set_running(item_key, "正在启动 PayPal 协议会话...")

        FlowClass = WebIdentityElevationPayPalFlowAdapter if task.flow_mode == "elevation" else WebPayPalFlowAdapter
        flow = FlowClass(
            ba_token=ba_token,
            user=user,
            card=card,
            address=address,
            max_card_attempts=3,
            proxy_config=proxy_cfg,
            task=task,
            item_key=item_key,
        )

        task.add_item_log(item_key, f"执行协议模式: {task.flow_mode.upper()}...")
        task.set_running(item_key, "正在执行 PayPal 页面初始化与指纹计算...")

        # 运行 PayPal 协议
        result = flow.run()

        is_success = bool(result and (result.get("status") == "success" or result.get("status") == "COMPLETED" or result.get("authorized")))
        if is_success:
            task.add_item_log(item_key, "🎉 PayPal 协议支付授权成功！Plus 订阅已即时开通。")
            res = {
                "status": "success",
                "label": "授权支付成功",
                "ba_token": ba_token,
                "buyer_email": user.email,
                "details": result,
            }
            # 更新数据库状态
            if email:
                db.update_plus_check(email, {
                    "status": "plus_active",
                    "label": "Plus生效中",
                    "plan": "plus",
                    "checked_at": time.time(),
                })
        else:
            err = result.get("error") or result.get("message") or "授权未成功完成"
            task.add_item_log(item_key, f"协议返回未完成: {err}")
            res = {
                "status": "error",
                "label": "授权失败",
                "error": err,
                "ba_token": ba_token,
            }
        task.mark_done(item_key, res)

    except Exception as e:
        err_msg = str(e)
        task.add_item_log(item_key, f"执行异常: {err_msg}")
        task.mark_done(item_key, {"status": "error", "label": "支付异常", "error": err_msg, "ba_token": ba_token})
    finally:
        try:
            loguru_logger.remove(sink_id)
        except Exception:
            pass


def start_paypal_pay_task(
    items: list[dict],
    workers: int = 2,
    country: str = "BR",
    flow_mode: str = "elevation",
    sms_provider_name: str = "",
    sms_api_key: str = "",
    sms_country: str = "52",
    proxy_pool: str = "",
) -> str:
    task_id = f"pay_{uuid.uuid4().hex[:8]}"
    task = PayPalPayJobTask(
        task_id=task_id,
        items=items,
        workers=workers,
        country=country,
        flow_mode=flow_mode,
        sms_provider_name=sms_provider_name,
        sms_api_key=sms_api_key,
        sms_country=sms_country,
        proxy_pool=proxy_pool,
    )
    with _pay_tasks_lock:
        _active_pay_tasks[task_id] = task

    def _runner():
        q: queue.Queue = queue.Queue()
        for k in task.items.keys():
            q.put(k)

        with ThreadPoolExecutor(max_workers=task.workers) as pool:
            futures = []
            for _ in range(task.workers):
                def _worker():
                    while not task.cancelled:
                        try:
                            k = q.get_nowait()
                        except queue.Empty:
                            break
                        try:
                            _execute_single_pay(task, k)
                        finally:
                            q.task_done()
                futures.append(pool.submit(_worker))
            for f in futures:
                try:
                    f.result()
                except Exception:
                    pass

        task.queue.put({"kind": "end"})

    threading.Thread(target=_runner, daemon=True, name=f"paypal-pay-{task_id}").start()
    return task_id


def stop_paypal_pay_task(task_id: str) -> bool:
    with _pay_tasks_lock:
        task = _active_pay_tasks.get(task_id)
        if not task:
            return False
        task.cancelled = True
        for cond in task.conditions.values():
            with cond:
                cond.notify_all()
        task.add_log("任务已手动停止")
        return True


def get_paypal_pay_task(task_id: str) -> Optional[PayPalPayJobTask]:
    with _pay_tasks_lock:
        return _active_pay_tasks.get(task_id)
