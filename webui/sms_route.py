"""Vak 接码线路调度：只在用户勾选的 (国家, 档位) 里选路。"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_ROUTE_COUNTRIES = (
    ("br", "巴西"),
    ("za", "南非"),
    ("cl", "智利"),
    ("gb", "英国"),
    ("it", "意大利"),
    ("au", "澳大利亚"),
)

STOCK_CACHE_SEC = 10.0
EMPTY_COOLDOWN_SEC = 45.0
LEDGER_SKIP_STREAK = 2


def default_sms_routes() -> list[dict]:
    return [
        {"country": iso, "price": "", "enabled": True, "priority": i + 1}
        for i, (iso, _name) in enumerate(DEFAULT_ROUTE_COUNTRIES)
    ]


def normalize_sms_routes(raw) -> list[dict]:
    rows = []
    if isinstance(raw, str) and raw.strip():
        import json
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    if not isinstance(raw, list):
        raw = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            continue
        country = str(item.get("country") or "").strip().lower()
        if not (len(country) == 2 and country.isalpha()):
            continue
        price = str(item.get("price") or item.get("price_key") or item.get("sms_max_price") or "").strip()
        enabled = item.get("enabled")
        if enabled is None:
            enabled = True
        try:
            priority = int(item.get("priority") or (i + 1))
        except (TypeError, ValueError):
            priority = i + 1
        rows.append({
            "country": country,
            "price": price,
            "enabled": bool(enabled),
            "priority": priority,
        })
    if not rows:
        return default_sms_routes()
    rows.sort(key=lambda r: (r["priority"], r["country"], r["price"]))
    return rows


class SmsRouteScheduler:
    """任务内共享：库存缓存、空档冷却、优先/轮转选路。"""

    def __init__(
        self,
        routes: list[dict],
        *,
        mode: str = "priority",
        fetch_tiers: Optional[Callable[[str], list[dict]]] = None,
        stock_cache_sec: float = STOCK_CACHE_SEC,
        empty_cooldown_sec: float = EMPTY_COOLDOWN_SEC,
        ledger_skip_streak: int = LEDGER_SKIP_STREAK,
        log_fn: Optional[Callable[[str], None]] = None,
    ):
        self.routes = [r for r in (routes or []) if r.get("enabled", True)]
        self.mode = "rotate" if str(mode or "").strip().lower() in ("rotate", "轮换", "spread") else "priority"
        self.fetch_tiers = fetch_tiers
        self.stock_cache_sec = max(3.0, float(stock_cache_sec or STOCK_CACHE_SEC))
        self.empty_cooldown_sec = max(5.0, min(600.0, float(empty_cooldown_sec or EMPTY_COOLDOWN_SEC)))
        self.ledger_skip_streak = max(1, min(20, int(ledger_skip_streak or LEDGER_SKIP_STREAK)))
        self.log_fn = log_fn
        self._lock = threading.Lock()
        self._cursor = 0
        self._stock: dict[str, tuple[float, list[dict]]] = {}
        self._cooldown: dict[tuple[str, str], float] = {}
        self._skip_streak: dict[tuple[str, str], int] = {}

    def _log(self, msg: str) -> None:
        if self.log_fn:
            try:
                self.log_fn(msg)
            except Exception:
                pass
        else:
            logger.info(msg)

    def _route_key(self, country: str, price: str) -> tuple[str, str]:
        return (str(country or "").lower(), str(price or "").strip())

    def mark_empty(self, country: str, price: str, reason: str = "") -> None:
        key = self._route_key(country, price)
        until = time.time() + self.empty_cooldown_sec
        with self._lock:
            self._cooldown[key] = until
            self._skip_streak[key] = 0
        self._log(
            f"线路 {country}@{price or '不限价'} 无货，冷却 {int(self.empty_cooldown_sec)}s"
            + (f" ({reason})" if reason else "")
        )

    def note_ledger_skip(self, country: str, price: str) -> bool:
        """台账命中脏号。同一线路连跳两次则冷却换线，避免上万拒号时死磕同一国家。"""
        key = self._route_key(country, price)
        with self._lock:
            n = int(self._skip_streak.get(key) or 0) + 1
            self._skip_streak[key] = n
        if n >= self.ledger_skip_streak:
            self.mark_empty(country, price, f"台账连跳{n}次")
            return True
        return False

    def note_ledger_ok(self, country: str, price: str) -> None:
        key = self._route_key(country, price)
        with self._lock:
            self._skip_streak[key] = 0

    def _tiers(self, country: str) -> list[dict]:
        iso = str(country or "").lower()
        now = time.time()
        with self._lock:
            cached = self._stock.get(iso)
            if cached and now - cached[0] < self.stock_cache_sec:
                return cached[1]
        tiers: list[dict] = []
        if self.fetch_tiers:
            try:
                tiers = list(self.fetch_tiers(iso) or [])
            except Exception as e:
                logger.debug("拉线路库存失败 country=%s: %s", iso, e)
                tiers = []
        with self._lock:
            self._stock[iso] = (now, tiers)
        return tiers

    def _live_count(self, country: str, price: str) -> int:
        tiers = self._tiers(country)
        if not price:
            return sum(int(t.get("count") or 0) for t in tiers)
        want = str(price).strip()
        try:
            want_p = float(want)
        except (TypeError, ValueError):
            want_p = -1
        total = 0
        for t in tiers:
            key = str(t.get("price_key") or t.get("price_str") or "")
            try:
                p = float(t.get("price") or 0)
            except (TypeError, ValueError):
                p = -1
            if key == want or (want_p > 0 and abs(p - want_p) <= 0.00015):
                total += int(t.get("count") or 0)
        return total

    def _eligible(self, now: float) -> list[dict]:
        with self._lock:
            cooldown = dict(self._cooldown)
            routes = list(self.routes)
        out = []
        for r in routes:
            country = r["country"]
            price = r.get("price") or ""
            key = self._route_key(country, price)
            until = cooldown.get(key) or 0
            if until > now:
                continue
            count = self._live_count(country, price)
            if count <= 0:
                continue
            item = dict(r)
            item["count"] = count
            item["lock"] = bool(price)
            out.append(item)
        return out

    def next_route(self) -> Optional[dict]:
        now = time.time()
        eligible = self._eligible(now)
        if not eligible:
            return None
        with self._lock:
            if self.mode == "rotate":
                n = len(eligible)
                idx = self._cursor % n
                self._cursor += 1
                picked = eligible[idx]
            else:
                eligible.sort(key=lambda r: (int(r.get("priority") or 99), -int(r.get("count") or 0)))
                picked = eligible[0]
        label = f"{picked['country']}@{picked.get('price') or '不限价'} 库存{picked.get('count')}"
        self._log(f"调度选路 {label} ({'轮转' if self.mode == 'rotate' else '优先'})")
        return picked

    def snapshot(self) -> list[dict]:
        now = time.time()
        rows = []
        for r in self.routes:
            key = self._route_key(r["country"], r.get("price") or "")
            until = self._cooldown.get(key) or 0
            rows.append({
                **r,
                "count": self._live_count(r["country"], r.get("price") or ""),
                "cooldown_left": max(0, int(until - now)),
            })
        return rows
