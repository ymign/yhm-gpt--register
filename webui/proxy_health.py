"""proxy_health.py — 代理出口 IP 智能评级、失败记忆与自动冷冻调度器
======================================================================
解决动态住宅代理因个别出口 IP 被 Cloudflare 或 OpenAI 标记导致的连续 409 / Warmup 失败问题。

核心机制：
1. 智能提取代理网关特征与出口 IP/Session 模板 (兼容各种 residential 格式)
2. 连续失败记忆：当某个代理连续 2 次触发 Warmup 失败、409 invalid_state 或网络拦截时，
   自动将该 IP 模板标记为「高风险」，进入 15 分钟静默冷冻期 (Cooldown)。
3. 智能路由过滤：全自动跑号或多 Worker 调度时，全自动剔除冷冻期代理，零浪费邮箱积分。
4. 成功即时解冻：一旦跑号成功自动清空失败计数，恢复全额健康评分。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Optional
from urllib.parse import unquote, urlsplit

logger = logging.getLogger("proxy_health")

DEFAULT_COOLDOWN_SECONDS = 900.0  # 冷冻 15 分钟
MAX_CONSECUTIVE_FAILS = 2         # 容忍连续失败 2 次即触发冷冻


class ProxyRecord:
    def __init__(self, key: str):
        self.key = key
        self.fail_count = 0
        self.success_count = 0
        self.last_fail_time = 0.0
        self.last_success_time = 0.0
        self.last_error = ""
        self.frozen_until = 0.0

    def record_fail(self, reason: str = "", cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        now = time.time()
        self.fail_count += 1
        self.last_fail_time = now
        self.last_error = str(reason or "")[:200]
        if self.fail_count >= MAX_CONSECUTIVE_FAILS:
            self.frozen_until = now + cooldown_seconds
            logger.warning(
                f"[ProxyHealth] ❄️ 代理特征 {self.key} 连续失败 {self.fail_count} 次 ({reason[:60]})，"
                f"已自动进入 {int(cooldown_seconds/60)} 分钟安全冷冻期！"
            )

    def record_ok(self):
        self.fail_count = 0
        self.success_count += 1
        self.last_success_time = time.time()
        self.frozen_until = 0.0
        self.last_error = ""

    def is_frozen(self) -> tuple[bool, str, int]:
        now = time.time()
        if self.frozen_until > now:
            rem = int(self.frozen_until - now)
            return True, self.last_error, rem
        return False, "", 0


class ProxyHealthManager:
    """全局代理健康度与自动冷冻中心。"""

    def __init__(self):
        self._records: dict[str, ProxyRecord] = {}
        self._lock = threading.Lock()

    def _extract_proxy_key(self, proxy_url: str) -> str:
        """归一化提取代理特征键（抹掉随机 session 段，保留网关与认证骨架）。"""
        p = str(proxy_url or "").strip()
        if not p:
            return ""
        try:
            if "://" not in p and "@" not in p and ":" in p:
                p = f"http://{p}"
            elif not p.startswith("http://") and not p.startswith("https://") and not p.startswith("socks5://") and not p.startswith("socks5h://"):
                p = f"http://{p}"
            parsed = urlsplit(p)
            username = unquote(parsed.username or "")
            # 抹平随机 session 段
            norm_user = re.sub(r"(?i)(-sid-|-session-|_session-)[a-z0-9]+", r"\g<1>*", username)
            host = parsed.hostname or ""
            port = parsed.port or ""
            return f"{norm_user}@{host}:{port}" if norm_user else f"{host}:{port}"
        except Exception:
            return p[:40]

    def record_failure(self, proxy_url: str, reason: str = "", cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        key = self._extract_proxy_key(proxy_url)
        if not key:
            return
        with self._lock:
            if key not in self._records:
                self._records[key] = ProxyRecord(key)
            self._records[key].record_fail(reason=reason, cooldown_seconds=cooldown_seconds)

    def record_success(self, proxy_url: str):
        key = self._extract_proxy_key(proxy_url)
        if not key:
            return
        with self._lock:
            if key in self._records:
                self._records[key].record_ok()

    def is_frozen(self, proxy_url: str) -> tuple[bool, str, int]:
        key = self._extract_proxy_key(proxy_url)
        if not key:
            return False, "", 0
        with self._lock:
            rec = self._records.get(key)
            if rec:
                return rec.is_frozen()
            return False, "", 0

    def filter_available_proxies(self, proxies: list[str]) -> list[str]:
        """从给定的代理池列表中过滤掉当前处于冷冻期的代理。"""
        if not proxies:
            return []
        available = []
        with self._lock:
            for p in proxies:
                if not p or not p.strip() or p.strip().startswith("#"):
                    continue
                key = self._extract_proxy_key(p)
                rec = self._records.get(key)
                if rec and rec.is_frozen()[0]:
                    continue
                available.append(p)
        return available if available else proxies  # 若全被冷冻则保底返回原列表尝试

    def get_summary(self) -> dict:
        now = time.time()
        with self._lock:
            total = len(self._records)
            frozen_count = sum(1 for r in self._records.values() if r.frozen_until > now)
            healthy_count = total - frozen_count
            items = []
            for r in self._records.values():
                is_fz, err, rem = r.is_frozen()
                items.append({
                    "key": r.key,
                    "fail_count": r.fail_count,
                    "success_count": r.success_count,
                    "is_frozen": is_fz,
                    "error": err,
                    "remaining_seconds": rem,
                })
            return {
                "total": total,
                "healthy": healthy_count,
                "frozen": frozen_count,
                "items": sorted(items, key=lambda x: x["is_frozen"], reverse=True)[:30],
            }


# 全局单例
_global_proxy_health = ProxyHealthManager()


def get_proxy_health_manager() -> ProxyHealthManager:
    return _global_proxy_health
