"""Plus 试用提链后台服务 (支持 PIX / UPI / Stripe 等全渠道提链与 SSE 实时监听)。

核心功能：
  1. 支持 CDK 余额与剩余次数查询
  2. 支持单账号与批量多 Worker 并发提链
  3. 支持 PIX (巴西)、UPI (印度)、Stripe Checkout 等多种渠道类型
  4. 支持 SSE 实时广播提炼事件流 (progress / log / result / end)
  5. 提链成功后自动回写 registered 数据库并支持格式化导出
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import requests

from . import db

logger = logging.getLogger(__name__)


def _api_base(override: Optional[str] = None) -> str:
    base = str(override or db.get_setting("extract_link_api_base", "")).strip().rstrip("/")
    if not base:
        raise ValueError("尚未配置提链服务 API 地址 (EXTRACT_LINK_API_BASE)")
    return base


def _cdk(override: Optional[str] = None) -> str:
    cdk_val = str(override or db.get_setting("extract_link_cdk", "")).strip()
    if not cdk_val:
        raise ValueError("尚未配置提链 CDK (EXTRACT_LINK_CDK)")
    return cdk_val


def _link_type(override: Optional[str] = None) -> str:
    t = str(override or db.get_setting("extract_link_type", "pix") or "pix").strip().lower()
    return t or "pix"


def query_cdk(api_base: Optional[str] = None, cdk: Optional[str] = None) -> dict:
    """查询指定或配置的 CDK 剩余次数。"""
    base = _api_base(api_base)
    code = _cdk(cdk)
    timeout = 15
    url = f"{base}/api/cdk?{urlencode({'code': code})}"
    try:
        resp = requests.get(url, headers={"Accept": "application/json"}, timeout=timeout)
        try:
            payload = resp.json()
        except Exception:
            payload = {"error": (resp.text or "")[:300]}
        if resp.status_code < 200 or resp.status_code >= 300:
            raise RuntimeError(payload.get("error") or payload.get("message") or f"HTTP {resp.status_code}")
        return payload if isinstance(payload, dict) else {}
    except Exception as e:
        raise RuntimeError(f"查询 CDK 失败: {e}")


def _create_extract_job(*, api_base: str, token: str, link_type: str, cdk: str, timeout: float = 30.0) -> dict:
    """向提链后端提交一个提取任务。"""
    url = f"{api_base}/api/extract"
    payload = {"link_type": link_type, "cdk": cdk, "token": token}
    resp = requests.post(url, json=payload, headers={"Accept": "application/json", "Content-Type": "application/json"}, timeout=timeout)
    try:
        data = resp.json()
    except Exception:
        data = {"error": (resp.text or "")[:300]}
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(data.get("error") or data.get("message") or f"HTTP {resp.status_code}")
    if not isinstance(data, dict) or not data.get("job_id"):
        raise RuntimeError(f"提链服务未返回有效 job_id: {data}")
    return data


def _iter_sse_events(*, api_base: str, job_id: str, cdk: str, timeout: float = 180.0):
    """监听提链服务端的 SSE 事件流。"""
    url = f"{api_base}/api/jobs/{quote(job_id, safe='')}/events?{urlencode({'cdk': cdk})}"
    resp = requests.get(url, headers={"Accept": "text/event-stream"}, stream=True, timeout=timeout)
    if resp.status_code < 200 or resp.status_code >= 300:
        raise RuntimeError(f"监听提链事件失败 HTTP {resp.status_code}: {(resp.text or '')[:300]}")

    event = "message"
    data_lines: list[str] = []
    for raw in resp.iter_lines():
        if raw is None:
            continue
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        line = line.rstrip("\r\n")
        if line == "":
            if data_lines:
                text = "\n".join(data_lines)
                try:
                    data = json.loads(text)
                except Exception:
                    data = {"raw": text}
                yield event, data
            event = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
    if data_lines:
        text = "\n".join(data_lines)
        try:
            data = json.loads(text)
        except Exception:
            data = {"raw": text}
        yield event, data


class ExtractTask:
    """单个批量提链任务管理器。"""

    def __init__(self, task_id: str, emails: list[str], config: dict):
        self.task_id = task_id
        self.config = config
        self.api_base = str(config.get("api_base") or "").strip().rstrip("/")
        self.cdk = str(config.get("cdk") or "").strip()
        self.link_type = str(config.get("link_type") or "pix").strip().lower()
        self.started_at = time.time()
        self.finished_at = 0.0

        self.items: dict[str, dict] = {
            e: {
                "email": e,
                "status": "pending",
                "step_text": "待处理",
                "link_type": self.link_type,
                "link_url": "",
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
        self.stats = {"success": 0, "error": 0}
        self._lock = threading.Lock()

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

    def set_running(self, email: str, step_text: str = "正在发起提链...") -> None:
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
            if st == "success":
                self.stats["success"] += 1
            else:
                self.stats["error"] += 1

            if email in self.items:
                it = self.items[email]
                it["status"] = st
                it["result"] = result
                it["link_url"] = result.get("link_url") or ""
                it["finished_at"] = now
                it["elapsed"] = round(now - (it["started_at"] or self.started_at), 1)
                it["step_text"] = result.get("label") or ("提链成功" if st == "success" else "提链失败")

        self.queue.put({
            "kind": "progress",
            "email": email,
            "status": st,
            "result": result,
            "link_url": result.get("link_url") or "",
            "step_text": result.get("label") or ("提链成功" if st == "success" else "提链失败"),
            "elapsed": self.items[email]["elapsed"] if email in self.items else 0,
        })


_extract_tasks: dict[str, ExtractTask] = {}
_tasks_lock = threading.Lock()


def _run_one_extract(task: ExtractTask, email: str) -> None:
    if task.cancelled:
        task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务已中止"})
        return

    task.set_running(email, step_text="正在创建提链任务...")
    task.add_email_log(email, f"开始提链: 渠道={task.link_type.upper()}")

    cred = db.get_registered(email)
    if not cred:
        res = {"status": "error", "label": "账号不存在", "error": "数据库中无此已注册账号"}
        task.add_email_log(email, "错误: 数据库中无此已注册账号")
        task.mark_done(email, res)
        return

    access_token = str(cred.get("access_token") or "").strip()
    if not access_token:
        res = {"status": "error", "label": "缺少Token", "error": "该账号缺少 access_token"}
        task.add_email_log(email, "错误: 该账号缺少 access_token，无法提链")
        task.mark_done(email, res)
        return

    started_ts = time.time()
    try:
        task.add_email_log(email, f"向提链服务提交任务 ({task.api_base})...")
        job = _create_extract_job(
            api_base=task.api_base,
            token=access_token,
            link_type=task.link_type,
            cdk=task.cdk,
            timeout=30.0,
        )
        job_id = str(job.get("job_id") or "")
        cdk_remaining = job.get("cdk_remaining")
        rem_tip = f" (CDK剩余: {cdk_remaining})" if cdk_remaining is not None else ""
        task.add_email_log(email, f"提链任务已创建: job_id={job_id[:12]}...{rem_tip}，正在监听事件流...")
        task.set_running(email, step_text="正在生成提链URL...")

        link_url = ""
        link_result = {}
        for event, data in _iter_sse_events(api_base=task.api_base, job_id=job_id, cdk=task.cdk, timeout=180.0):
            if task.cancelled:
                task.mark_done(email, {"status": "cancelled", "label": "已取消", "error": "任务已中止"})
                return

            if event == "log":
                msg = str((data or {}).get("message") or "")
                if msg:
                    task.add_email_log(email, f"[服务日志] {msg}")
            elif event == "result":
                link_result = (data or {}).get("result") if isinstance(data, dict) else {}
                if not isinstance(link_result, dict):
                    link_result = {}
                # 尝试解析多种可能的链接字段
                link_url = str(
                    link_result.get("url")
                    or link_result.get("link")
                    or link_result.get("checkout_url")
                    or link_result.get("payment_url")
                    or (data or {}).get("url")
                    or ""
                ).strip()
                break
            elif event == "error":
                err_obj = (data or {}).get("error") if isinstance(data, dict) else None
                msg = (err_obj.get("message") if isinstance(err_obj, dict) else None) or str(data)
                raise RuntimeError(msg or "提链服务端返回失败")
            elif event == "done":
                break

        req_ms = int((time.time() - started_ts) * 1000)
        if not link_url and isinstance(link_result, dict):
            # 若结果是字典且没有 url 字段，尝试序列化
            for k, v in link_result.items():
                if isinstance(v, str) and ("http://" in v or "https://" in v):
                    link_url = v
                    break

        if not link_url:
            link_url = str(link_result or "提炼成功")

        # 回写数据库 extra_json
        db.update_registered_extract(
            email=email,
            extract_data={
                "status": "success",
                "link_type": task.link_type,
                "link_url": link_url,
                "job_id": job_id,
                "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "result": link_result,
            },
        )

        res = {
            "status": "success",
            "label": "提链成功",
            "link_url": link_url,
            "job_id": job_id,
            "link_type": task.link_type,
            "result": link_result,
            "req_ms": req_ms,
        }
        task.add_email_log(email, f"🎉 提链成功 ({req_ms}ms): {link_url}")
        task.mark_done(email, res)

    except Exception as e:
        req_ms = int((time.time() - started_ts) * 1000)
        err_str = str(e)
        task.add_email_log(email, f"提链异常 ({req_ms}ms): {err_str}")
        res = {"status": "error", "label": "提链失败", "error": err_str, "req_ms": req_ms}
        db.update_registered_extract(
            email=email,
            extract_data={
                "status": "failed",
                "link_type": task.link_type,
                "error": err_str,
                "extracted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
        )
        task.mark_done(email, res)


def _worker_loop(task: ExtractTask, email_queue: queue.Queue) -> None:
    while not task.cancelled:
        try:
            email = email_queue.get_nowait()
        except queue.Empty:
            break
        try:
            _run_one_extract(task, email)
        finally:
            email_queue.task_done()


def start(emails: list[str], config: dict) -> str:
    """启动批量提链任务。"""
    unique_emails = list(dict.fromkeys(e.strip().lower() for e in emails if e and e.strip()))
    if not unique_emails:
        raise ValueError("请提供至少一个要提链的账号邮箱")

    api_base = _api_base(config.get("api_base"))
    cdk_val = _cdk(config.get("cdk"))
    link_type_val = _link_type(config.get("link_type"))
    workers = max(1, min(16, int(config.get("workers") or 3)))

    task_id = str(uuid.uuid4())[:12]
    task_config = {
        "api_base": api_base,
        "cdk": cdk_val,
        "link_type": link_type_val,
        "workers": workers,
    }
    task = ExtractTask(task_id, unique_emails, task_config)

    with _tasks_lock:
        if len(_extract_tasks) > 20:
            for k in list(_extract_tasks.keys())[:-10]:
                _extract_tasks.pop(k, None)
        _extract_tasks[task_id] = task

    email_queue: queue.Queue = queue.Queue()
    for em in unique_emails:
        email_queue.put(em)

    def _run():
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix=f"extract_{task_id}") as pool:
            futures = [pool.submit(_worker_loop, task, email_queue) for _ in range(workers)]
            for f in futures:
                try:
                    f.result()
                except Exception as e:
                    logger.warning(f"[extract] Worker 异常: {e}")

        task.finished_at = time.time()
        try:
            task.queue.put({"kind": "end", "task_id": task_id, "stats": task.stats})
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name=f"ExtractTaskRunner-{task_id}")
    t.start()
    return task_id


def stop(task_id: str) -> bool:
    with _tasks_lock:
        task = _extract_tasks.get(task_id)
        if task:
            task.cancelled = True
            try:
                task.queue.put({"kind": "end", "task_id": task_id, "cancelled": True})
            except Exception:
                pass
            return True
    return False


def snapshot(task_id: str) -> Optional[dict]:
    with _tasks_lock:
        task = _extract_tasks.get(task_id)
        if not task:
            return None
        with task._lock:
            return {
                "task_id": task.task_id,
                "api_base": task.api_base,
                "link_type": task.link_type,
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
        task = _extract_tasks.get(task_id)
        return task.queue if task else None


def get_logs(task_id: str, email: str) -> list[str]:
    with _tasks_lock:
        task = _extract_tasks.get(task_id)
        if not task:
            return []
        with task._lock:
            it = task.items.get(email.lower().strip())
            return list(it["logs"]) if it else []
