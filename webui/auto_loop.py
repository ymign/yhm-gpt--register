"""auto-loop 控制器：多 worker 并发，每个 worker 用独立代理。

设计：
  - 主控线程 manage_loop：监听 stop/pause、根据 concurrency 启停 worker
  - 多个 worker 线程：claim_next() → 注册 → 完成 → 继续
  - 代理池：每个 worker 按 worker index 取一个代理（round-robin），避免同 IP 多号
  - 状态机：stopped → running → paused → running / stopped
  - 优雅暂停/停止：当前 worker 跑完才退出，不强杀
  - 复用 registrar.start_registration：每个号开一个 run，由 worker 等其结束
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from typing import Optional

from . import db, registrar
from mail_providers import MailProviderError, get_provider_class

logger = logging.getLogger("auto_loop")


class AutoLoopState:
    STOPPED = "stopped"
    RUNNING = "running"
    PAUSED = "paused"


def _parse_proxy_pool(text: str) -> list[str]:
    """把多行代理字符串拆成列表。空行 / # 开头注释跳过。"""
    out: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


class AutoLoopController:
    """多 worker auto-loop 控制器。

    options 关键字段：
      proxy:                单代理（兼容旧版，concurrency=1 时用）
      proxy_pool:           多代理字符串（每行一个；多 worker 会按 worker index 轮流取）
      concurrency:          并发 worker 数（1-20）
      cool_down_seconds:    每个 worker 跑完后冷却时间（默认 3）
      其余参数透传给 registrar.start_registration
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._state = AutoLoopState.STOPPED
        self._manage_thread: Optional[threading.Thread] = None
        self._workers: list[threading.Thread] = []
        self._options: dict = {}
        self._stop_event = threading.Event()
        self._pause_event = threading.Event()  # set = 暂停
        # 进度统计
        self._started_at: float = 0.0
        self._finished_at: float = 0.0
        self._registered_ok = 0
        self._registered_fail = 0
        # 当前每个 worker 在跑啥（worker_id → email）
        self._worker_status: dict[int, dict] = {}
        self._worker_cycles: dict[int, int] = {}
        self._last_message = ""
        # 熔断与风控状态
        self._consecutive_network_fails = 0
        self._consecutive_409_count = 0
        self._circuit_break_threshold = 3
        self._last_break_reason = ""
        self._risk_backoff_until = 0.0
        # 速度与出号率波形监控 (CPM Velocity)
        self._ok_timestamps: list[float] = []
        self._velocity_history: list[dict] = []
        self._last_velocity_sample_time: float = 0.0
        self._used_proxies_set: set[str] = set()
        # SSE 订阅
        self._subscribers: list[queue.Queue] = []
        # 代理池 / 并发数
        self._proxy_pool: list[str] = []
        self._concurrency: int = 1
        # 实际拉起的 worker 数：目标数量 >0 时不超过剩余目标，避免 15 个 worker 抢 1 个名额
        self._spawn_count: int = 1
        # 目标成功数：0 = 不限量（保持旧行为）；>0 时累计成功达标即自动停止
        self._target_count: int = 0
        # 任务流水列表（最新 200 条，用于前端表格展示每一个号的进度与日志）
        self._tasks: list[dict] = []
        self._tasks_map: dict[str, dict] = {}

    # ──────────────────────── 公共 API ────────────────────────

    def start(self, options: dict) -> dict:
        with self._lock:
            if self._state in (AutoLoopState.RUNNING, AutoLoopState.PAUSED):
                return {"ok": False, "error": f"已经在跑了 (state={self._state})"}
            # 重置
            self._stop_event.clear()
            self._pause_event.clear()
            self._options = dict(options or {})
            self._state = AutoLoopState.RUNNING
            self._started_at = time.time()
            self._finished_at = 0.0
            self._registered_ok = 0
            self._registered_fail = 0
            self._worker_status.clear()
            self._worker_cycles.clear()
            self._ok_timestamps.clear()
            self._velocity_history.clear()
            self._used_proxies_set.clear()
            self._last_velocity_sample_time = 0.0
            self._consecutive_network_fails = 0
            self._consecutive_409_count = 0
            self._risk_backoff_until = 0.0
            self._last_message = "auto-loop 启动"
            # 解析并发参数（上限 50：纯协议注册本机余量充足，真正上限在代理池与风控）
            self._concurrency = max(1, min(50, int(self._options.get("concurrency") or 1)))
            pool_text = self._options.get("proxy_pool") or ""
            self._proxy_pool = _parse_proxy_pool(pool_text)
            # 目标成功数（0=不限量）
            self._target_count = max(0, int(self._options.get("target_count") or 0))
            # 目标=1 却开 15 worker 时，多余线程会立刻「已锁定，退出」，日志刷屏、看起来像没开始。
            # 实际并发不超过目标数量；同一 worker 失败后会自己重试直到达标。
            self._spawn_count = self._concurrency
            if self._target_count:
                self._spawn_count = max(1, min(self._concurrency, self._target_count))
            # 连续网络错误自动暂停阈值（0=关闭熔断）
            raw_cbt = self._options.get("circuit_break_threshold")
            if raw_cbt is None:
                raw_cbt = self._options.get("autoCircuitBreak", 3)
            try:
                self._circuit_break_threshold = max(0, int(raw_cbt))
            except Exception:
                self._circuit_break_threshold = 3
            if self._spawn_count != self._concurrency:
                logger.info(
                    f"auto-loop 启动: concurrency={self._concurrency} → 实际 {self._spawn_count} "
                    f"(目标 {self._target_count})，circuit_break_threshold={self._circuit_break_threshold}"
                )
            else:
                logger.info(
                    f"auto-loop 启动: concurrency={self._concurrency}, "
                    f"circuit_break_threshold={self._circuit_break_threshold}, "
                    f"target_count={self._target_count}"
                )
            # 启 manage 线程
            self._manage_thread = threading.Thread(
                target=self._manage_loop, daemon=True, name="auto-loop-manage"
            )
            self._manage_thread.start()
        self._broadcast("state", self._snapshot())
        return {
            "ok": True,
            "state": self._state,
            "concurrency": self._concurrency,
            "proxy_pool_size": len(self._proxy_pool),
            "target_count": self._target_count,
        }

    def pause(self) -> dict:
        with self._lock:
            if self._state != AutoLoopState.RUNNING:
                return {"ok": False, "error": f"当前 state={self._state}，不可暂停"}
            self._pause_event.set()
            self._state = AutoLoopState.PAUSED
            self._last_message = "已请求暂停（当前 worker 跑完才生效）"
        self._broadcast("state", self._snapshot())
        return {"ok": True, "state": self._state}

    def resume(self) -> dict:
        with self._lock:
            if self._state != AutoLoopState.PAUSED:
                return {"ok": False, "error": f"当前 state={self._state}，不可恢复"}
            self._pause_event.clear()
            self._state = AutoLoopState.RUNNING
            self._last_message = "已恢复"
        self._broadcast("state", self._snapshot())
        return {"ok": True, "state": self._state}

    def stop(self) -> dict:
        with self._lock:
            if self._state == AutoLoopState.STOPPED:
                return {"ok": False, "error": "没在跑"}
            self._stop_event.set()
            self._pause_event.clear()
            self._last_message = "已请求停止（当前 worker 跑完才生效）"
        self._broadcast("state", self._snapshot())
        return {"ok": True}

    def status(self) -> dict:
        return self._snapshot()

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=100)
        with self._lock:
            self._subscribers.append(q)
        try:
            q.put_nowait({"kind": "state", "data": self._snapshot()})
        except queue.Full:
            pass
        return q

    def unsubscribe(self, q: queue.Queue):
        with self._lock:
            try: self._subscribers.remove(q)
            except ValueError: pass

    # ──────────────────────── 内部 ────────────────────────

    def _snapshot(self) -> dict:
        with self._lock:
            stats = db.stats()
            now = time.time()

            elapsed = 0.0
            if self._started_at:
                if self._state in (AutoLoopState.RUNNING, AutoLoopState.PAUSED):
                    elapsed = now - self._started_at
                elif self._finished_at:
                    elapsed = self._finished_at - self._started_at

            # 1. 速度与出号率 CPM 计算 (过去 60 秒与过去 300 秒)
            recent_1m = [ts for ts in self._ok_timestamps if now - ts <= 60]
            recent_5m = [ts for ts in self._ok_timestamps if now - ts <= 300]
            cpm = float(len(recent_1m))
            if self._state == AutoLoopState.RUNNING and 0 < elapsed < 60 and self._registered_ok > 0:
                cpm = round((self._registered_ok / elapsed) * 60, 1)

            cpm_5m = round((len(recent_5m) / min(300.0, max(1.0, elapsed))) * 60, 1) if elapsed > 0 else 0.0
            projected_hourly = int(round(cpm * 60)) if cpm > 0 else int(round(cpm_5m * 60))
            tot_finished = self._registered_ok + self._registered_fail
            success_rate = round((self._registered_ok / tot_finished) * 100, 1) if tot_finished > 0 else 0.0

            # 2. 定时记录速率曲线采样点（每 2 秒记录一次，最多存 30 个点）
            if now - self._last_velocity_sample_time >= 2.0:
                self._last_velocity_sample_time = now
                self._velocity_history.append({
                    "t": round(now, 1),
                    "cpm": round(cpm, 1),
                })
                if len(self._velocity_history) > 30:
                    self._velocity_history.pop(0)

            # 3. 智能风控状态监控与冷冻统计
            cooling_down_count = 0
            try:
                from .proxy_health import get_proxy_health_manager
                cooling_down_count = get_proxy_health_manager().get_summary().get("cooling_down_count", 0)
            except Exception:
                pass

            risk_active = bool(
                self._consecutive_409_count >= 3
                or (self._state == AutoLoopState.PAUSED and ("连续" in self._last_break_reason or "409" in self._last_break_reason))
                or (cooling_down_count >= 3 and self._consecutive_409_count > 0)
            )
            backoff_left = max(0, int(self._risk_backoff_until - now)) if self._risk_backoff_until > now else 0
            risk_warning = {
                "active": risk_active,
                "consecutive_409": self._consecutive_409_count,
                "frozen_proxies": cooling_down_count,
                "backoff_seconds_left": backoff_left,
                "reason": (
                    f"⚠️ 触发风控频控保护：检测到连续 {self._consecutive_409_count} 次 409 IP 频控或出口风控拦截，已自动冷冻 {cooling_down_count} 个异常代理并启动保护"
                    if risk_active else ""
                ),
            }

            # 4. 多 Worker 多核动态舰队大屏数据 (Fleet HUD)
            fleet_info = []
            target_country = self._options.get("proxy_country", "")
            fleet_n = getattr(self, "_spawn_count", None) or self._concurrency
            for wid in range(fleet_n):
                if wid in self._worker_status:
                    info = self._worker_status[wid]
                    rid = info.get("run_id", "")
                    p_info = registrar.get_run_phase(rid) if rid else {}
                    phase = p_info.get("phase", "running")
                    phase_text = p_info.get("phase_text", "正在注册...")
                    pct = p_info.get("percent", 15)

                    # 步骤微动画阶段链 (1~5)
                    step_idx = 1
                    if phase in ("sentinel", "pow"):
                        step_idx = 1
                    elif phase in ("otp_sent", "otp_verify"):
                        step_idx = 2
                    elif phase in ("register_pw", "password", "official_password"):
                        step_idx = 3
                    elif phase in ("binding_2fa", "2fa_done"):
                        step_idx = 4
                    elif phase in ("creating", "done"):
                        step_idx = 5

                    st_ts = info.get("started_at", now)
                    elapsed_w = round(now - st_ts, 1)
                    w_proxy = info.get("proxy", "")
                    country = registrar.extract_proxy_country(w_proxy, target_country)

                    fleet_info.append({
                        "id": wid,
                        "status": "running",
                        "email": info.get("email", ""),
                        "run_id": rid,
                        "proxy": w_proxy,
                        "country": country,
                        "started_at": st_ts,
                        "elapsed": elapsed_w,
                        "phase": phase,
                        "phase_text": phase_text,
                        "percent": pct,
                        "step_index": step_idx,
                        "cycles": self._worker_cycles.get(wid, 0),
                        "last_error": "",
                    })
                else:
                    fleet_info.append({
                        "id": wid,
                        "status": "cooling" if self._state == AutoLoopState.RUNNING else self._state,
                        "email": "",
                        "run_id": "",
                        "proxy": self._proxy_for_worker(wid) if self._state == AutoLoopState.RUNNING else "",
                        "country": target_country,
                        "started_at": 0,
                        "elapsed": 0,
                        "phase": "idle",
                        "phase_text": "准备就绪 / 领取下一个号..." if self._state == AutoLoopState.RUNNING else "空闲待命",
                        "percent": 0,
                        "step_index": 0,
                        "cycles": self._worker_cycles.get(wid, 0),
                        "last_error": "",
                    })

            # 计算 tasks 的动态耗时与实时步骤
            tasks_copy = []
            for t in self._tasks:
                item = dict(t)
                rid = item.get("run_id")
                # 实时同步真实邮箱（从 placeholder 升级为分配/复用的真正邮箱）
                if rid and "placeholder" in (item.get("email") or ""):
                    try:
                        cur_em = db._conn().execute("SELECT email FROM runs WHERE run_id=?", (rid,)).fetchone()
                        if cur_em and cur_em["email"] and "placeholder" not in cur_em["email"]:
                            item["email"] = cur_em["email"]
                            t["email"] = cur_em["email"]
                    except Exception:
                        pass
                if item.get("status") == "running":
                    if item.get("started_at"):
                        item["elapsed"] = round(now - item["started_at"], 1)
                    if rid:
                        p_info = registrar.get_run_phase(rid)
                        item["phase"] = p_info.get("phase", item.get("phase", "running"))
                        item["phase_text"] = p_info.get("phase_text", item.get("phase_text", "正在注册..."))
                        item["percent"] = p_info.get("percent", 15)
                tasks_copy.append(item)

            return {
                "state": self._state,
                "started_at": self._started_at,
                "finished_at": self._finished_at,
                "elapsed": elapsed,
                "registered_ok": self._registered_ok,
                "registered_fail": self._registered_fail,
                "target_count": self._target_count,
                "remaining": (
                    max(0, self._target_count - self._registered_ok)
                    if self._target_count else None
                ),
                "concurrency": self._concurrency,
                "spawn_count": getattr(self, "_spawn_count", self._concurrency),
                "circuit_break_threshold": self._circuit_break_threshold,
                "proxy_pool_size": len(self._proxy_pool),
                "workers": fleet_info,
                "fleet": fleet_info,
                "velocity": {
                    "cpm": cpm,
                    "cpm_5m": cpm_5m,
                    "projected_hourly": projected_hourly,
                    "success_rate": success_rate,
                    "proxies_used": len(self._used_proxies_set),
                    "history": self._velocity_history,
                },
                "risk_warning": risk_warning,
                "tasks": tasks_copy,
                "last_message": self._last_message,
                "pool_stats": stats,
            }

    def _broadcast(self, kind: str, data):
        with self._lock:
            subs = list(self._subscribers)
        for q in subs:
            try:
                q.put_nowait({"kind": kind, "data": data})
            except queue.Full:
                pass

    def _set_message(self, msg: str):
        with self._lock:
            self._last_message = msg
        self._broadcast("state", self._snapshot())

    def _try_reserve_slot(self, worker_id: int) -> bool:
        """占一个在跑名额。已占过的 worker 直接放行；目标已满返回 False。"""
        with self._lock:
            if worker_id in self._worker_status:
                return True
            if self._target_count and (
                self._registered_ok + len(self._worker_status) >= self._target_count
            ):
                return False
            self._worker_status[worker_id] = {
                "email": "",
                "run_id": "",
                "proxy": "",
                "target_country": "",
                "started_at": time.time(),
                "reserved": True,
            }
            return True

    def _release_slot(self, worker_id: int) -> None:
        with self._lock:
            self._worker_status.pop(worker_id, None)

    def _proxy_for_worker(self, worker_id: int) -> str:
        """按 worker_id 从代理池里挑一个可用代理（自动跳过处于 15 分钟风控冷冻期或黑名单的代理）。

        整模板已被拉黑或连续失败冷冻的代理条目跳过；全池被拉黑时回退全量并告警 ——
        宁可用脏代理也不能让 worker 没代理可用。
        """
        pool = self._proxy_pool
        if pool:
            try:
                from . import db
                from .proxy_util import normalize_proxy_key
                from .proxy_health import get_proxy_health_manager
                bad_templates = db.get_blacklist()["templates"]
            except Exception:
                bad_templates = set()

            usable = [p for p in pool if normalize_proxy_key(p) not in bad_templates] if bad_templates else list(pool)

            # 过滤掉处于 15 分钟失败冷冻期的代理
            try:
                from .proxy_health import get_proxy_health_manager
                active_proxies = get_proxy_health_manager().filter_available_proxies(usable)
                if active_proxies:
                    usable = active_proxies
            except Exception:
                pass

            if usable:
                return usable[worker_id % len(usable)]

            logger.warning(
                f"[auto-loop] 代理池 {len(pool)} 个代理模板已被拉黑或处于冷冻期，回退使用全量池"
            )
            return pool[worker_id % len(pool)]
        return self._options.get("proxy", "") or ""

    def _record_finish(self, ok: bool, category: str, error_msg: str = "", worker_id: int = 0):
        """worker 结束一个 run 后调，更新计数 + 熔断 + 速度与风控记录。"""
        with self._lock:
            now_ts = time.time()
            if ok:
                self._registered_ok += 1
                self._consecutive_network_fails = 0
                self._consecutive_409_count = 0
                self._ok_timestamps.append(now_ts)
                cutoff = now_ts - 7200
                self._ok_timestamps = [ts for ts in self._ok_timestamps if ts >= cutoff]
            else:
                self._registered_fail += 1
                if category == "network":
                    self._consecutive_network_fails += 1
                else:
                    self._consecutive_network_fails = 0

                err_str = f"{category} {error_msg}".lower()
                if "409" in err_str or "conflict" in err_str or "cf_challenge" in err_str:
                    self._consecutive_409_count += 1
                    self._risk_backoff_until = now_ts + 60.0
                else:
                    self._consecutive_409_count = 0

            self._worker_cycles[worker_id] = self._worker_cycles.get(worker_id, 0) + (1 if ok else 0)
            self._last_message = (
                f"累计 ok={self._registered_ok} fail={self._registered_fail}"
            )
            # 目标数量：累计成功达标 → 触发停止（stop_event 幂等，多 worker 同时命中也安全）
            target_reached = bool(
                self._target_count and self._registered_ok >= self._target_count
            )
            trigger_break = bool(
                self._circuit_break_threshold > 0
                and self._consecutive_network_fails >= self._circuit_break_threshold
                and self._state == AutoLoopState.RUNNING
            )

        if target_reached:
            with self._lock:
                self._stop_event.set()
                self._last_message = (
                    f"🎯 已达目标 {self._target_count} 个，自动停止"
                    f"（成功 {self._registered_ok} / 失败 {self._registered_fail}）"
                )
            logger.info(f"已达目标 {self._target_count} 个成功，触发自动停止")
            self._broadcast("state", self._snapshot())
            return

        if trigger_break:
            with self._lock:
                self._pause_event.set()
                self._state = AutoLoopState.PAUSED
                self._last_break_reason = (
                    f"连续 {self._consecutive_network_fails} 次网络/环境错误（已达设定的 {self._circuit_break_threshold} 次阈值），"
                    f"自动暂停（号已自动 release，请检查代理后点恢复）"
                )
                self._last_message = self._last_break_reason
                self._consecutive_network_fails = 0
            logger.warning(self._last_break_reason)
            self._broadcast("circuit_break", {"reason": self._last_break_reason})

    def _manage_loop(self):
        """主控线程：启动 worker，等所有 worker 结束，更新最终状态。"""
        heartbeat_stop = threading.Event()

        def _heartbeat():
            while not heartbeat_stop.is_set():
                if self._state in (AutoLoopState.RUNNING, AutoLoopState.PAUSED):
                    self._broadcast("state", self._snapshot())
                heartbeat_stop.wait(1.2)

        hb_thread = threading.Thread(target=_heartbeat, daemon=True, name="auto-loop-heartbeat")
        hb_thread.start()

        try:
            workers = []
            spawn_n = getattr(self, "_spawn_count", None) or self._concurrency
            for wid in range(spawn_n):
                t = threading.Thread(
                    target=self._worker_loop, args=(wid,),
                    daemon=True, name=f"auto-loop-worker-{wid}",
                )
                t.start()
                workers.append(t)
                # 每个 worker 之间错开 1s 启动，避免同时打 OpenAI
                time.sleep(1.0)
            self._workers = workers
            # 等所有 worker 退出
            for t in workers:
                t.join()
        except Exception as e:
            logger.exception(f"manage_loop 异常: {e}")
        finally:
            heartbeat_stop.set()
            with self._lock:
                self._state = AutoLoopState.STOPPED
                self._finished_at = time.time()
                self._worker_status.clear()
                self._last_message = (
                    f"已停止（成功 {self._registered_ok} / 失败 {self._registered_fail}）"
                )
            self._broadcast("state", self._snapshot())

    def _worker_loop(self, worker_id: int):
        """单 worker 循环：claim → 跑 → 等结束 → 继续。"""
        idle_round = 0
        last_proxy = ""
        logger.info(f"[worker-{worker_id}] 启动")

        while True:
            # 检查停止
            if self._stop_event.is_set():
                self._release_slot(worker_id)
                logger.info(f"[worker-{worker_id}] 已停止")
                return

            # 检查暂停
            if self._pause_event.is_set():
                while self._pause_event.is_set() and not self._stop_event.is_set():
                    time.sleep(0.5)
                if self._stop_event.is_set():
                    self._release_slot(worker_id)
                    return

            # 目标数量闸门：先占坑再开跑，避免两个 worker 同时看到「还没人在跑」一起冲进去。
            # 占不到就短等，不要 return——上一发失败后还得有人接着跑。
            if not self._try_reserve_slot(worker_id):
                if self._stop_event.is_set():
                    logger.info(f"[worker-{worker_id}] 已停止")
                    return
                time.sleep(1.0)
                continue

            # claim 下一个号。要不要走号池由 provider 的 pooled 决定，
            # 非池化的（CF 这类自己造地址的）用虚拟占位。
            mail_source = (self._options.get("mail_source") or db.get_setting("mail_source", "cf_temp")).strip().lower()
            try:
                pooled = get_provider_class(mail_source).pooled
            except MailProviderError as e:
                logger.error(f"[worker-{worker_id}] {e}，停止")
                self._set_message(str(e))
                self._release_slot(worker_id)
                return
            if pooled:
                account = db.claim_next(kind=mail_source)
            else:
                account = {
                    "email": f"{mail_source}_placeholder_"
                             f"{int(time.time())}_{worker_id}@placeholder.local",
                    "password": "", "client_id": "", "refresh_token": "",
                    "relay_url": "", "kind": mail_source,
                }
            if not account:
                idle_round += 1
                if idle_round == 1:
                    self._set_message(
                        f"worker-{worker_id} 号池空，等待新号..."
                    )
                # 空 10 轮（约 30s）就停掉这个 worker
                if idle_round >= 10:
                    logger.info(f"[worker-{worker_id}] 号池空 30s，停止")
                    self._release_slot(worker_id)
                    return
                # 等 3s 再试
                for _ in range(30):
                    if self._stop_event.is_set() or self._pause_event.is_set():
                        break
                    time.sleep(0.1)
                continue
            idle_round = 0

            # 给这个 run 注入 worker 自己的代理与邮箱来源。
            # 代理每轮重新取：池子稳定时 worker_id % len 结果不变（代理固定），
            # 有代理被健康度拉黑时 usable 列表变化 → 该 worker 自动切到新代理，
            # 不用等整批跑完重启。
            proxy = self._proxy_for_worker(worker_id)
            if proxy != last_proxy:
                logger.info(f"[worker-{worker_id}] 使用代理: {proxy or '直连'}")
                last_proxy = proxy
            run_options = dict(self._options)
            run_options["mail_source"] = mail_source
            if proxy:
                run_options["proxy"] = proxy

            # 记录使用过的代理
            if proxy:
                with self._lock:
                    self._used_proxies_set.add(proxy)

            # 启一个 run
            try:
                run_id = registrar.start_registration(account, run_options)
            except Exception as e:
                logger.exception(f"[worker-{worker_id}] 启动注册失败: {e}")
                self._release_slot(worker_id)
                if pooled:
                    db.release_unused(account["email"])
                time.sleep(2)
                continue

            target_country = self._options.get("proxy_country", "") or ""
            now_ts = time.time()
            task_item = {
                "run_id": run_id,
                "email": account["email"],
                "worker_id": worker_id,
                "proxy": proxy,
                "target_country": target_country,
                "status": "running",
                "phase": "starting",
                "phase_text": "正在注册...",
                "started_at": now_ts,
                "finished_at": None,
                "elapsed": 0,
                "error": "",
                "reg_country": target_country,
                "reg_city": "",
                "reg_ip": "",
            }

            with self._lock:
                self._worker_status[worker_id] = {
                    "email": account["email"],
                    "run_id": run_id,
                    "proxy": proxy,
                    "target_country": target_country,
                    "started_at": now_ts,
                }
                self._tasks_map[run_id] = task_item
                self._tasks.insert(0, task_item)
                if len(self._tasks) > 200:
                    old_task = self._tasks.pop()
                    self._tasks_map.pop(old_task.get("run_id", ""), None)

            self._broadcast("state", self._snapshot())
            self._broadcast("run_started", {
                "worker_id": worker_id,
                "email": account["email"],
                "run_id": run_id,
                "proxy": proxy,
                "target_country": target_country,
            })

            # 等当前 run 跑完
            ok, category = self._wait_run_finish(run_id)

            finish_ts = time.time()
            with self._lock:
                self._worker_status.pop(worker_id, None)
                if run_id in self._tasks_map:
                    t = self._tasks_map[run_id]
                    t["status"] = "done" if ok else "failed"
                    t["finished_at"] = finish_ts
                    t["elapsed"] = round(finish_ts - t["started_at"], 1)

                    # 同步真实邮箱
                    try:
                        cur_em = db._conn().execute("SELECT email FROM runs WHERE run_id=?", (run_id,)).fetchone()
                        if cur_em and cur_em["email"] and "placeholder" not in cur_em["email"]:
                            t["email"] = cur_em["email"]
                    except Exception:
                        pass

                    if ok:
                        t["phase"] = "done"
                        t["phase_text"] = "注册完成"
                        # 从 db 获取出口信息
                        try:
                            reg = db.get_registered(t.get("email") or account["email"])
                            if reg:
                                t["reg_country"] = reg.get("reg_country", "")
                                t["reg_city"] = reg.get("reg_city", "")
                                t["reg_ip"] = reg.get("reg_ip", "")
                        except Exception:
                            pass
                    else:
                        t["phase"] = "failed"
                        # 查 error 详情
                        err_msg = ""
                        try:
                            cur = db._conn().execute("SELECT error FROM runs WHERE run_id=?", (run_id,))
                            row_err = cur.fetchone()
                            if row_err and row_err["error"]:
                                err_msg = str(row_err["error"])
                        except Exception:
                            pass
                        t["error"] = err_msg or category or "注册失败"
                        t["phase_text"] = f"失败: {t['error'][:40]}"

            error_detail = t.get("error", "") if "t" in locals() and isinstance(t, dict) else ""
            self._record_finish(ok, category, error_msg=error_detail, worker_id=worker_id)
            self._broadcast("state", self._snapshot())
            self._broadcast("run_finished", {
                "worker_id": worker_id,
                "email": account["email"],
                "run_id": run_id,
                "ok": ok,
                "category": category,
            })

            # 冷却（每个 worker 自己的节奏）
            cool_down = float(self._options.get("cool_down_seconds") or 3)
            if cool_down > 0:
                for _ in range(int(cool_down * 10)):
                    if self._stop_event.is_set() or self._pause_event.is_set():
                        break
                    time.sleep(0.1)

    def _wait_run_finish(self, run_id: str, timeout: int = 1800) -> tuple[bool, str]:
        """轮询 runs 表，等 run 跑完。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self._stop_event.is_set():
                return False, ""
            con = db._conn()
            cur = con.execute(
                "SELECT status, error_category FROM runs WHERE run_id=?", (run_id,)
            )
            row = cur.fetchone()
            if row:
                st = row["status"]
                if st == "done":
                    return True, ""
                if st == "failed":
                    return False, (row["error_category"] or "")
            time.sleep(0.8)
        logger.warning(f"run {run_id} 等了 {timeout}s 没结束，超时放弃")
        return False, ""


# 全局单例
CONTROLLER = AutoLoopController()
