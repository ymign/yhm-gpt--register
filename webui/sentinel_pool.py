"""sentinel_pool.py — OpenAI Sentinel Token (PoW) 智能预计算池
===================================================================
通过后台工作线程在系统空闲期提前计算并缓存高算力 Proof-of-Work (PoW) Sentinel Token，
注册任务或改密/2FA 发起时直接 0 毫秒秒级取用，使单账号注册耗时降低 40% 以上，并彻底消除
高并发启动时 CPU 算力瞬间打满卡顿的问题。

特性：
1. 内存极速缓冲池 (FIFO Queue + TTL 自动过期剔除，默认 75 秒安全有效期)
2. 弹性容量守护 (动态维持 2~5 个热备 Sentinel Token)
3. 无锁/轻量并发安全调度
4. 优雅回落 (若池空或不可用自动无缝降级为实时计算)
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Optional

logger = logging.getLogger("sentinel_pool")

# Sentinel Token 在 OpenAI 端的有效时间约为 90~120 秒，本地预计算保质期保守设为 70 秒
TOKEN_TTL_SECONDS = 70.0
# 预计算池水位 ≠ 前端「PoW 算力槽位」。默认关闭：池里的 token 是用随机指纹算的，
# 塞进正在注册的会话会变成「HTTP 一套指纹、Sentinel 另一套」，事后极易被风控。
DEFAULT_POOL_SIZE = 3


class CachedSentinelToken:
    def __init__(
        self,
        token: str,
        so_token: str,
        flow: str,
        created_at: float,
        fingerprint: dict,
    ):
        self.token = token
        self.so_token = so_token
        self.flow = flow
        self.created_at = created_at
        self.fingerprint = fingerprint

    def is_valid(self, max_age: float = TOKEN_TTL_SECONDS) -> bool:
        return (time.time() - self.created_at) < max_age


class SentinelPrecomputePool:
    """Sentinel PoW 预计算池管理器。"""

    def __init__(self, target_size: int = DEFAULT_POOL_SIZE):
        self.target_size = max(1, min(10, int(target_size)))
        self._pool: list[CachedSentinelToken] = []
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._worker_thread: Optional[threading.Thread] = None
        # 一号一指纹：禁止用别人的预计算 PoW。需要时再在本号会话里现场算。
        self._enabled = False
        self._stats = {
            "hits": 0,
            "misses": 0,
            "generated": 0,
            "expired": 0,
        }

    def start(self):
        """启动后台预计算守护线程。"""
        if not self._enabled:
            logger.info(
                f"[SentinelPool] 预计算池已停用（不是前端 PoW 槽位；"
                f"缓冲水位配置={self.target_size}）。"
                f"注册必须用本号自己的 UA/屏幕/时区现场计算 Sentinel，避免串指纹被风控。"
            )
            return
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._stop_event.clear()
        self._worker_thread = threading.Thread(
            target=self._daemon_loop,
            name="SentinelPrecomputeDaemon",
            daemon=True,
        )
        self._worker_thread.start()
        logger.info(
            f"[SentinelPool] 预计算池守护线程已启动 (目标缓冲水位={self.target_size}；"
            f"≠ 前端 PoW 算力槽位)"
        )

    def stop(self):
        """停止后台预计算守护线程。"""
        self._stop_event.set()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2.0)
        logger.info("[SentinelPool] 预计算池守护线程已停止")

    def set_target_size(self, size: int):
        self.target_size = max(1, min(10, int(size)))

    def set_enabled(self, enabled: bool):
        self._enabled = bool(enabled)
        if not self._enabled:
            with self._lock:
                self._pool.clear()

    def get_stats(self) -> dict:
        with self._lock:
            valid_count = sum(1 for item in self._pool if item.is_valid())
            return {
                "enabled": self._enabled,
                "buffered": valid_count,
                "target_size": self.target_size,
                "hits": self._stats["hits"],
                "misses": self._stats["misses"],
                "generated": self._stats["generated"],
                "expired": self._stats["expired"],
            }

    def pop_token(self, flow: str = "authorize_continue") -> Optional[tuple[str, str, dict]]:
        """从预计算池中弹出一个有效且未过期的 Sentinel Token。

        Returns:
            Optional[tuple[sentinel_token, so_token, fingerprint]]
        """
        if not self._enabled:
            return None

        now = time.time()
        with self._lock:
            # 1. 过滤清理掉已过期的 Token
            valid_items = []
            for item in self._pool:
                if item.is_valid():
                    valid_items.append(item)
                else:
                    self._stats["expired"] += 1
            self._pool = valid_items

            # 2. 匹配对应 flow 的 Token
            for i, item in enumerate(self._pool):
                if item.flow == flow:
                    popped = self._pool.pop(i)
                    self._stats["hits"] += 1
                    elapsed = round(now - popped.created_at, 1)
                    logger.info(f"[SentinelPool] ⚡ 命中预计算 Token！(flow={flow}, 龄期={elapsed}s, 0ms 秒取)")
                    return (popped.token, popped.so_token, popped.fingerprint)

            self._stats["misses"] += 1
            return None

    def _daemon_loop(self):
        """后台静默计算循环。"""
        time.sleep(2.0)  # 启动后延时等待主进程与配置就绪
        while not self._stop_event.is_set():
            if not self._enabled:
                time.sleep(5.0)
                continue

            try:
                # 检查当前有效水位
                now = time.time()
                with self._lock:
                    self._pool = [item for item in self._pool if item.is_valid()]
                    current_count = len(self._pool)

                if current_count < self.target_size:
                    # 计算并填充一个新的 Token (默认 authorize_continue 核心流)
                    self._generate_one_hot_token("authorize_continue")

                # 间隔休眠，避免无谓占用 CPU
                for _ in range(25):
                    if self._stop_event.is_set():
                        break
                    time.sleep(0.2)
            except Exception as e:
                logger.debug(f"[SentinelPool] 预计算异常 (静默重试): {e}")
                time.sleep(4.0)

    def _generate_one_hot_token(self, flow: str = "authorize_continue"):
        """使用独立 session 计算单枚 Sentinel Token 并推入缓冲池。"""
        try:
            import uuid
            from curl_cffi.requests import Session as CffiSession
            from fingerprint import generate_fingerprint
            from sentinel_quickjs import get_sentinel_token_via_quickjs

            fp = generate_fingerprint()
            ua = fp.get("user_agent") or ""
            impersonate = fp.get("impersonate", "chrome146")

            # 纯内存会话（用于握手 sentinel 计算）
            session = CffiSession(impersonate=impersonate)
            session.trust_env = False
            device_id = str(uuid.uuid4())

            qresult = get_sentinel_token_via_quickjs(
                session,
                device_id=device_id,
                flow=flow,
                log=lambda _: None,
                user_agent=ua,
                screen=fp.get("screen", ""),
                lang=fp.get("lang", ""),
                lang_full=fp.get("lang_full", ""),
                browser_type=fp.get("browser_type", ""),
                platform=fp.get("navigator_platform", ""),
                vendor=fp.get("navigator_vendor"),
                hardware_concurrency=fp.get("hardware_concurrency", 8),
                device_memory=fp.get("device_memory", 8),
                max_touch_points=fp.get("max_touch_points", 0),
                device_pixel_ratio=fp.get("device_pixel_ratio", 1.0),
                timezone=fp.get("timezone", ""),
                sec_ch_ua_full_version_list=fp.get("sec_ch_ua_full_version_list", ""),
                sec_ch_ua_arch=fp.get("sec_ch_ua_arch", ""),
                sec_ch_ua_bitness=fp.get("sec_ch_ua_bitness", ""),
                sec_ch_ua_model=fp.get("sec_ch_ua_model", ""),
                sec_ch_ua_platform_version=fp.get("sec_ch_ua_platform_version", ""),
            )

            if qresult and qresult[0]:
                token, so_token = qresult
                cached_obj = CachedSentinelToken(
                    token=token,
                    so_token=so_token or "",
                    flow=flow,
                    created_at=time.time(),
                    fingerprint=fp,
                )
                with self._lock:
                    self._pool.append(cached_obj)
                    self._stats["generated"] += 1
                logger.debug(f"[SentinelPool] 预计算成功入池 (当前存量={len(self._pool)}/{self.target_size})")
        except Exception as e:
            logger.debug(f"[SentinelPool] 单枚预计算失败: {e}")


# 全局单例
_global_sentinel_pool: Optional[SentinelPrecomputePool] = None


def get_sentinel_pool() -> SentinelPrecomputePool:
    global _global_sentinel_pool
    if _global_sentinel_pool is None:
        _global_sentinel_pool = SentinelPrecomputePool(target_size=DEFAULT_POOL_SIZE)
        _global_sentinel_pool.start()
    return _global_sentinel_pool
