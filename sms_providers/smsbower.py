"""SmsBower：sms-activate 协议接码。HeroSMS 共用此实现，仅 base_url 不同。"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

from .base import BaseSmsProvider, SmsActivation, register
from .util import (
    OPENAI_SMS_COUNTRIES,
    SMS_COUNTRY_NAMES_CN,
    SMS_DEFAULT_COUNTRY,
    SMS_DEFAULT_SERVICE,
    parse_price_spec,
)

logger = logging.getLogger(__name__)

_SMS_CACHE_LOCK = threading.Lock()
_SMS_CACHE: Optional[dict] = None


def _project_cache_dir() -> Path:
    root = Path(__file__).resolve().parents[1]
    cache = root / "data"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _smsbower_cache_file() -> Path:
    return _project_cache_dir() / ".smsbower_phone_cache.json"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()

def _parse_sms_status_text(text: str) -> dict:
    text = str(text or "").strip()
    if not text or text == "STATUS_WAIT_CODE":
        return {"status": "wait_code"}
    if text == "STATUS_CANCEL":
        return {"status": "cancel"}

    # 1. 优先提取冒号后的内容（兼容 STATUS_OK / STATUS_WAIT_RETRY / STATUS_WAIT_RESEND 等任意前缀携带的验证码）
    if ":" in text:
        parts = text.split(":", 1)
        prefix = parts[0].strip().upper()
        rest = parts[1].strip()
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", rest)
        if m:
            return {"status": "ok", "code": m.group(1), "raw": text}
        if prefix in ("STATUS_OK", "ACCESS_ACTIVATION"):
            return {"status": "ok", "code": rest, "raw": text}

    # 2. 如果包含 6 位连续数字且不是错误响应
    if not text.startswith("ERROR") and not text.startswith("BAD") and not text.startswith("NO_"):
        m = re.search(r"(?<!\d)(\d{6})(?!\d)", text)
        if m:
            return {"status": "ok", "code": m.group(1), "raw": text}

    if text.startswith("STATUS_WAIT_RETRY"):
        return {"status": "wait_retry", "raw": text}
    if text.startswith("STATUS_WAIT_RESEND"):
        return {"status": "wait_resend", "raw": text}

    return {"status": "unknown", "raw": text}


def _make_sms_candidate(activation_id: str, source: str, code) -> Optional[dict]:
    code = str(code or "").strip()
    if not code or code in {"null", "None"}:
        return None
    return {
        "status": "ok",
        "code": code,
        "source": source,
        "sms_key": hashlib.sha256(
            f"{activation_id}:{code}".encode("utf-8")
        ).hexdigest(),
    }

@register
class SmsBowerProvider(BaseSmsProvider):
    """sms-activate 协议系 provider（SmsBower / HeroSMS 共用）。"""

    DEFAULT_BASE_URL = "https://smsbower.page/stubs/handler_api.php"

    kind = "smsbower"
    aliases = ("sms_bower",)
    display_name = "SmsBower"
    short_label = "即退款"
    description = "遇到手机号验证时自动租号收码，未接通即时取消即退款"
    sort_order = 10
    needs_api_key = True
    uses_cdk_pool = False
    uses_country = True
    uses_price_tiers = True
    uses_provider_ids = True
    uses_reuse_phone = True
    uses_auto_country = True
    default_country = "52"
    default_service = "dr"
    default_timeout = 80
    recommended_timeout = 80
    max_timeout = 90
    timeout_hint = "推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。"

    @classmethod
    def from_config(cls, config: dict) -> "SmsBowerProvider":
        from .util import (
            parse_price_spec, _safe_float, _safe_int, _safe_bool, SMS_DEFAULT_COUNTRY,
        )
        api_key = str(config.get("sms_api_key") or "").strip()
        if not api_key and cls.needs_api_key:
            raise RuntimeError(f"{cls.display_name or cls.kind} 未配置 API Key")
        country = str(config.get("sms_country") or "").strip()
        service = str(config.get("sms_service") or "").strip() or "dr"
        proxy = (str(config.get("sms_proxy") or "")).strip() or None
        price_spec = config.get("sms_price") or config.get("sms_max_price") or config.get("sms_price_spec")
        min_p, max_p, exact_p = parse_price_spec(price_spec)
        if "sms_min_price" in config:
            min_p = _safe_float(config.get("sms_min_price"), min_p)
        if "sms_exact_price" in config:
            exact_p = _safe_float(config.get("sms_exact_price"), exact_p)
        reuse = _safe_bool(config.get("sms_reuse_phone"), False)
        succ_max = max(0, _safe_int(config.get("sms_phone_success_max"), 3))
        provider_ids = str(
            config.get("sms_provider_ids")
            or config.get("providerIds")
            or config.get("sms_operator")
            or config.get("operator")
            or ""
        ).strip()
        except_provider_ids = str(
            config.get("sms_except_provider_ids") or config.get("exceptProviderIds") or ""
        ).strip()
        phone_exception = str(
            config.get("sms_phone_exception") or config.get("phoneException") or ""
        ).strip()
        return cls(
            api_key=api_key,
            base_url=getattr(cls, "DEFAULT_BASE_URL", "") or "",
            default_service=service,
            default_country=country or cls.default_country or SMS_DEFAULT_COUNTRY,
            max_price=max_p,
            min_price=min_p,
            exact_price=exact_p,
            price_spec=price_spec,
            operator=provider_ids,
            provider_ids=provider_ids,
            except_provider_ids=except_provider_ids,
            phone_exception=phone_exception,
            proxy=proxy,
            reuse_phone_to_max=reuse,
            phone_success_max=succ_max,
        )
    auto_report_success_on_code = False  # 等业务侧确认才报成功（便于号码复用）

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "",
        default_service: str = SMS_DEFAULT_SERVICE,
        default_country: str = SMS_DEFAULT_COUNTRY,
        max_price: float = -1,
        min_price: float = -1,
        exact_price: float = -1,
        price_spec=None,
        operator: str = "",
        provider_ids: str = "",
        except_provider_ids: str = "",
        phone_exception: str = "",
        proxy: Optional[str] = None,
        reuse_phone_to_max: bool = True,
        phone_success_max: int = 3,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip() or self.DEFAULT_BASE_URL
        self.default_service = str(default_service or SMS_DEFAULT_SERVICE).strip()
        self.default_country = str(default_country or SMS_DEFAULT_COUNTRY).strip()
        self.operator = str(operator or "").strip()
        self.provider_ids = str(provider_ids or operator or "").strip()
        self.except_provider_ids = str(except_provider_ids or "").strip()
        self.phone_exception = str(phone_exception or "").strip()

        parsed_min, parsed_max, parsed_exact = parse_price_spec(price_spec)
        self.min_price = parsed_min if parsed_min > 0 else float(min_price or -1)
        self.max_price = parsed_max if parsed_max > 0 else float(max_price or -1)
        self.exact_price = parsed_exact if parsed_exact > 0 else float(exact_price or -1)

        self._proxy = (proxy or "").strip() or None
        self._proxies = {"http": self._proxy, "https": self._proxy} if self._proxy else None
        self.reuse_phone_to_max = bool(reuse_phone_to_max)
        self.phone_success_max = max(0, int(phone_success_max or 0))
        self._resend_callback: Optional[Callable[[], None]] = None
        self.last_code_result: Optional[dict] = None
        self.current_activation: Optional[SmsActivation] = None

    # ---- HTTP ----

    def _request(self, params: dict, *, needs_key: bool = True, timeout: int = 30) -> requests.Response:
        payload = dict(params)
        if needs_key:
            payload["api_key"] = self.api_key
        resp = requests.get(self.base_url, params=payload, timeout=timeout, proxies=self._proxies)
        resp.raise_for_status()
        return resp

    # ---- 余额 / 价格 / 国家 ----

    def get_balance(self) -> float:
        text = self._request({"action": "getBalance"}).text.strip()
        if text.startswith("ACCESS_BALANCE:"):
            return float(text.split(":", 1)[1])
        raise RuntimeError(f"SmsBower getBalance 失败: {text}")

    def get_prices(self, service: Optional[str] = None, country=None) -> dict:
        params = {"action": "getPrices"}
        if service:
            params["service"] = service
        if country not in (None, ""):
            params["country"] = country
        data = self._request(params).json()
        if isinstance(data, dict):
            return data
        raise RuntimeError("SmsBower getPrices 返回结构异常")

    def get_top_countries(self, service: Optional[str] = None) -> list[dict]:
        """按实时库存汇总国家列表，数据源与官网/线路档位一致（getPricesV3 优先）。

        旧实现一旦 getTopCountriesByService 成功就直接返回，那份排名经常缺国家，
        官网明明有货（如智利 150）下拉里却显示「暂无库存」。
        """
        service_code = str(service or self.default_service or SMS_DEFAULT_SERVICE).strip()
        merged: dict[str, dict] = {}

        def _absorb(rows: list[dict]) -> None:
            for r in rows or []:
                cid = str(r.get("country") or "").strip()
                if not cid:
                    continue
                prev = merged.get(cid)
                if not prev:
                    merged[cid] = {
                        "country": cid,
                        "price": r.get("price"),
                        "count": int(r.get("count") or 0),
                    }
                    continue
                try:
                    new_c = int(r.get("count") or 0)
                except (TypeError, ValueError):
                    new_c = 0
                try:
                    old_c = int(prev.get("count") or 0)
                except (TypeError, ValueError):
                    old_c = 0
                prev["count"] = max(old_c, new_c)
                np, op = r.get("price"), prev.get("price")
                try:
                    nf = float(np) if np is not None else None
                except (TypeError, ValueError):
                    nf = None
                try:
                    of = float(op) if op is not None else None
                except (TypeError, ValueError):
                    of = None
                if nf is not None and nf > 0 and (of is None or nf < of):
                    prev["price"] = nf

        # 1) 与网页点选同一套：getPricesV3 全量（按线路汇总库存）
        for action in ("getPricesV3", "getPricesV2", "getPrices"):
            try:
                data = self._request(
                    {"action": action, "service": service_code},
                    timeout=45,
                ).json()
                _absorb(self._aggregate_country_stock(data, service_code))
            except Exception as exc:
                logger.warning("SmsBower %s 拉国家库存失败: %s", action, exc)

        # 2) 排名接口只作补缺，不再单独作为唯一数据源
        if not merged:
            for action in ("getTopCountriesByServiceRank", "getTopCountriesByService"):
                try:
                    data = self._request({"action": action, "service": service_code}).json()
                    _absorb(self._parse_top_countries(data))
                    if merged:
                        break
                except Exception:
                    continue

        rows = [v for v in merged.values() if int(v.get("count") or 0) > 0 or v.get("price") is not None]
        rows.sort(key=lambda r: (r.get("price") if r.get("price") is not None else 999, -(r.get("count") or 0)))
        return rows

    @staticmethod
    def _aggregate_country_stock(data, service_code: str) -> list[dict]:
        """从 getPrices / V2 / V3 JSON 汇总每个国家的最低价和总库存。"""
        rows = []
        if not isinstance(data, dict):
            return rows
        for cid, blob in data.items():
            try:
                cid_s = str(int(str(cid).strip()))
            except (TypeError, ValueError):
                continue
            if not isinstance(blob, dict):
                continue
            svc = blob.get(service_code) if service_code in blob else blob
            if not isinstance(svc, dict):
                continue

            total = 0
            min_price = None

            def _add(price_val, count_val):
                nonlocal total, min_price
                try:
                    c = int(count_val or 0)
                except (TypeError, ValueError):
                    c = 0
                try:
                    p = float(price_val) if price_val is not None and str(price_val) != "" else None
                except (TypeError, ValueError):
                    p = None
                if c > 0:
                    total += c
                if p is not None and p > 0 and (min_price is None or p < min_price):
                    min_price = p

            # V1：{cost, count}
            if any(k in svc for k in ("cost", "count", "price", "qty", "available")) and not any(
                isinstance(v, dict) and ("price" in v or "count" in v or "provider_id" in v)
                for v in svc.values()
            ):
                _add(svc.get("cost") or svc.get("price"), svc.get("count") or svc.get("qty") or svc.get("available"))
            else:
                # V3：{providerId: {price, count, provider_id}}
                nested = svc
                if service_code in nested and isinstance(nested.get(service_code), dict):
                    nested = nested[service_code]
                for item in nested.values() if isinstance(nested, dict) else []:
                    if not isinstance(item, dict):
                        continue
                    _add(
                        item.get("price") or item.get("cost"),
                        item.get("count") or item.get("qty") or item.get("available"),
                    )

            if total > 0 or min_price is not None:
                rows.append({"country": cid_s, "price": min_price, "count": total})
        return rows

    @staticmethod
    def _parse_top_countries(data) -> list[dict]:
        rows = []
        items = data
        if isinstance(data, dict):
            items = data.get("data") or data.get("result") or data.get("response") or data
        if isinstance(items, dict):
            for key, value in items.items():
                if not isinstance(value, dict):
                    continue
                try:
                    country_id = str(int(key))
                except (TypeError, ValueError):
                    continue
                price = value.get("price") or value.get("cost") or value.get("retail_price")
                count = value.get("count") or value.get("qty") or value.get("available") or 0
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                try:
                    count = int(count) if count is not None else 0
                except (TypeError, ValueError):
                    count = 0
                if price is not None:
                    rows.append({"country": country_id, "price": price, "count": count})
        elif isinstance(items, list):
            for item in items:
                if not isinstance(item, dict):
                    continue
                country_id = item.get("country") or item.get("countryId") or item.get("country_id") or item.get("id")
                if country_id is None:
                    continue
                price = item.get("price") or item.get("cost")
                count = item.get("count") or item.get("qty") or item.get("available") or 0
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                try:
                    count = int(count) if count is not None else 0
                except (TypeError, ValueError):
                    count = 0
                if price is not None:
                    rows.append({"country": str(country_id), "price": price, "count": count})
        return rows

    def get_country_price_tiers(self, country: str, service: Optional[str] = None) -> list[dict]:
        """只查指定业务（默认 OpenAI = dr）的实时线路/金额/库存，不混入其它业务。"""
        service_code = str(service or self.default_service or "dr").strip() or "dr"
        country_id = str(country or self.default_country or "6").strip()
        queries = [
            {"action": "getPricesV3", "service": service_code, "country": country_id},
            {"action": "getPricesV2", "service": service_code, "country": country_id},
            {"action": "getPrices", "service": service_code, "country": country_id},
        ]
        last_err = None
        for params in queries:
            try:
                resp = self._request(params, timeout=45)
                text = (resp.text or "").strip()
                if resp.status_code != 200 or not text.startswith("{"):
                    continue
                data = resp.json()
                tiers = self._tiers_from_price_json(data, country_id, service_code)
                if tiers:
                    logger.info(
                        "SmsBower 号池档位 country=%s service=%s via %s → %d 条",
                        country_id, service_code, params.get("action"), len(tiers),
                    )
                    return tiers
            except Exception as exc:
                last_err = exc
                logger.warning(
                    "SmsBower get_country_price_tiers %s country=%s 失败: %s",
                    params, country_id, exc,
                )
        if last_err:
            logger.warning(f"SmsBower get_country_price_tiers 查询失败 (country={country_id}): {last_err}")
        return []

    @classmethod
    def _iter_price_nodes(cls, obj, parent_key: str = ""):
        """递归找出 {price/count} 节点，以及 V2 的 { '0.072': 30 } 价库映射。"""
        found = []
        if not isinstance(obj, dict):
            return found
        child_dicts = {k: v for k, v in obj.items() if isinstance(v, dict)}
        looks_provider = any(k in obj for k in ("price", "cost", "provider_id"))
        nested_providers = any(
            isinstance(v, dict) and any(x in v for x in ("price", "cost", "count", "provider_id"))
            for v in child_dicts.values()
        )
        if looks_provider and not nested_providers:
            found.append((parent_key, obj))
            return found

        v2_pairs = []
        only_scalar = not child_dicts
        if only_scalar and obj:
            ok = True
            for k, v in obj.items():
                try:
                    float(k)
                    int(v)
                    v2_pairs.append((str(k), v))
                except (TypeError, ValueError):
                    ok = False
                    break
            if ok and v2_pairs:
                for k, v in v2_pairs:
                    found.append((k, {"price": k, "count": v, "provider_id": ""}))
                return found

        for k, v in obj.items():
            if isinstance(v, dict):
                found.extend(cls._iter_price_nodes(v, str(k)))
        return found

    @classmethod
    def _tiers_from_price_json(cls, data, country_id: str, service_code: str) -> list[dict]:
        if not isinstance(data, dict):
            return []
        blob = data
        for key in (country_id, str(country_id).lstrip("0") or country_id):
            if key in blob and isinstance(blob[key], dict):
                blob = blob[key]
                break
        else:
            try:
                ik = int(country_id)
            except (TypeError, ValueError):
                ik = None
            if ik is not None and ik in blob and isinstance(blob[ik], dict):
                blob = blob[ik]

        scoped = None
        if isinstance(blob, dict):
            if service_code in blob and isinstance(blob[service_code], dict):
                scoped = blob[service_code]
            else:
                service_like = [
                    k for k, v in blob.items()
                    if isinstance(v, dict) and re.fullmatch(r"[a-z]{1,4}", str(k) or "")
                ]
                if service_like:
                    # 该国有多业务分区，但没有 OpenAI/dr，不把其它业务（tg/wa/ot…）混进来
                    return []
                scoped = blob
        if scoped is None:
            return []
        nodes = cls._iter_price_nodes(scoped)

        tiers = []
        seen = set()
        for key, item in nodes:
            if not isinstance(item, dict):
                continue
            try:
                pid = str(item.get("provider_id") or item.get("id") or key or "").strip()
                raw_price = item.get("price") if item.get("price") is not None else item.get("cost")
                p = float(raw_price) if raw_price is not None and str(raw_price) != "" else 0.0
                c = int(item.get("count") or item.get("qty") or item.get("available") or 0)
            except (TypeError, ValueError):
                continue
            if c <= 0 or p < 0:
                continue
            uniq = (pid, round(p, 6), c)
            if uniq in seen:
                continue
            seen.add(uniq)
            c_str = f"{c}件" if c < 10000 else f"{round(c / 10000, 2)}万件"
            price_str = str(raw_price)
            label = f"{pid} · {p} $ (余 {c_str})" if pid else f"{p} $ (余 {c_str})"
            tiers.append({
                "id": pid,
                "provider_id": pid,
                "price": p,
                "price_str": price_str,
                "count": c,
                "label": label,
                "tag_label": f"{p} $ (余 {c_str})",
            })
        tiers.sort(key=lambda x: (x["price"], -x["count"]))
        return tiers

    def _provider_ids_cheaper_than(self, country: str, service: str, price_floor: float) -> str:
        """从 getPricesV3 收集所有单价低于目标档位的供应商 ID，供 exceptProviderIds 使用。"""
        if price_floor <= 0:
            return ""
        ids: list[str] = []
        seen: set[str] = set()
        try:
            for t in self.get_country_price_tiers(country=country, service=service):
                pid = str(t.get("id") or t.get("provider_id") or "").strip()
                try:
                    p = float(t.get("price") or 0)
                except (TypeError, ValueError):
                    continue
                if not pid or pid in seen:
                    continue
                if p > 0 and p < price_floor - 1e-5:
                    seen.add(pid)
                    ids.append(pid)
        except Exception as exc:
            logger.warning("SmsBower 收集更低价供应商失败: %s", exc)
        return ",".join(ids)

    def get_best_country(self, service: Optional[str] = None, *,
                         min_stock: int = 20, max_price: float = 0,
                         strict_whitelist: bool = False,
                         allowed_countries: Optional[list[str]] = None) -> Optional[str]:
        """自动选最优国家。

        allowed_countries 优先级最高（用户自定义 = 从这些国家里挑最便宜+库存足的）
        strict_whitelist  = True → 只从 OPENAI_SMS_COUNTRIES 选（即 52 泰国）
        都没设 → 全部国家自由选（默认；用户自行承担"OpenAI 让用 WhatsApp"的风险）
        """
        try:
            rows = self.get_top_countries(service=service)
        except Exception as exc:
            logger.warning("SmsBower get_best_country 查询失败: %s", exc)
            return None
        if not rows:
            return None

        allowed_set: Optional[set[str]] = None
        if allowed_countries:
            allowed_set = {str(c).strip() for c in allowed_countries if str(c).strip()}

        def _pick(stock_threshold: int) -> Optional[str]:
            for row in rows:
                cid = str(row.get("country") or "")
                # 优先用 user-supplied 白名单
                if allowed_set is not None:
                    if cid not in allowed_set:
                        continue
                elif strict_whitelist and cid not in OPENAI_SMS_COUNTRIES:
                    continue
                price = row.get("price") or 0
                count = row.get("count") or 0
                if count < stock_threshold:
                    continue
                if max_price > 0 and price > max_price:
                    continue
                # 非白名单国家 → warn 一下（不阻止）
                if not strict_whitelist and cid not in OPENAI_SMS_COUNTRIES:
                    logger.warning(
                        "SmsBower 自动选了非 OpenAI-SMS 白名单国家 country=%s price=%s "
                        "（OpenAI 可能让此号用 WhatsApp 验证 → 收不到 SMS）",
                        cid, price,
                    )
                return cid
            return None

        return _pick(min_stock) or _pick(1)

    # ---- 号码复用缓存 ----

    def _cache_identity(self, service: str, country: str) -> dict:
        return {
            "api_key_hash": _hash_secret(self.api_key),
            "service": str(service),
            "country": str(country),
        }

    def _load_cache(self, service: str, country: str) -> Optional[dict]:
        global _SMS_CACHE
        cache = _SMS_CACHE
        if cache is None:
            path = _smsbower_cache_file()
            if not path.exists():
                return None
            try:
                cache = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        identity = self._cache_identity(service, country)
        if any(str(cache.get(k) or "") != str(v) for k, v in identity.items()):
            return None
        elapsed = time.time() - float(cache.get("acquired_at") or 0)
        if elapsed >= SMS_PHONE_LIFETIME or cache.get("reuse_stopped"):
            self._clear_cache()
            return None
        if self.phone_success_max > 0 and int(cache.get("use_count") or 0) >= self.phone_success_max:
            cache["reuse_stopped"] = True
            cache["stop_reason"] = f"success max reached ({self.phone_success_max})"
            self._save_cache(cache)
            return None
        cache["used_codes"] = set(cache.get("used_codes") or [])
        _SMS_CACHE = cache
        return cache

    def _save_cache(self, cache: Optional[dict]) -> None:
        global _SMS_CACHE
        _SMS_CACHE = cache
        path = _smsbower_cache_file()
        if cache is None:
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return
        serializable = dict(cache)
        serializable["used_codes"] = sorted(serializable.get("used_codes") or [])
        path.write_text(json.dumps(serializable, ensure_ascii=False), encoding="utf-8")

    def _clear_cache(self) -> None:
        self._save_cache(None)

    # ---- 租号 ----

    def _request_number_single_action(self, action: str, service: str, country: str) -> dict:
        """单次调用 getNumberV2 或 getNumber（严格按价格要求筛选）。

        优化逻辑：
          1. 若设置了 exact_price / min_price / max_price，向平台接口传入参数；
          2. 若平台分配了不符合设定价格的号码（如设置0.008却返回了0.007劣质号），
             在 0.1 秒内自动 cancel 免费退号，并在单次租号流程中自动向平台重试索要指定价格号；
          3. 上层调用方与 OpenAI 验证链路将仅接收 100% 符合金额的号码，绝不浪费时间在非目标号码上！
        """
        min_p = self.min_price
        max_p = self.max_price
        exact_p = self.exact_price
        if exact_p > 0:
            min_p = exact_p
            max_p = exact_p

        wanted_ops = [x.strip() for x in str(self.provider_ids or "").replace(";", ",").split(",") if x.strip()]
        lock_by_operator = bool(wanted_ops)

        except_ids = str(self.except_provider_ids or "").strip()
        # 没指定线路、只锁金额时才排除更便宜档。点选 3237 后按线路租，不再叠 minPrice。
        if not lock_by_operator and (exact_p > 0 or min_p > 0):
            floor_price = exact_p if exact_p > 0 else min_p
            cheaper_ids = self._provider_ids_cheaper_than(country, service, floor_price)
            except_ids = ",".join(x for x in (except_ids, cheaper_ids) if x)

        payload_with_price = {"action": action, "service": service, "country": country}
        if wanted_ops:
            payload_with_price["providerIds"] = ",".join(wanted_ops)
        if except_ids:
            payload_with_price["exceptProviderIds"] = except_ids
        if self.phone_exception:
            payload_with_price["phoneException"] = self.phone_exception
        if max_p > 0:
            payload_with_price["maxPrice"] = max_p
        if min_p > 0 and not lock_by_operator:
            payload_with_price["minPrice"] = min_p

        last_resp_text = ""
        max_price_retries = 8 if (min_p > 0 or exact_p > 0 or except_ids or lock_by_operator) else 1
        extra_except: list[str] = []
        tried_operator_fallback = False

        def _merged_except() -> str:
            parts = [x.strip() for x in except_ids.split(",") if x.strip()]
            for x in extra_except:
                if x and x not in parts:
                    parts.append(x)
            return ",".join(parts)

        for retry_idx in range(max_price_retries):
            params = dict(payload_with_price)
            merged = _merged_except()
            if merged:
                params["exceptProviderIds"] = merged
            price_desc_list = []
            if exact_p > 0:
                price_desc_list.append(f"锁定={exact_p}")
            elif min_p > 0 and max_p > 0:
                price_desc_list.append(f"区间={min_p}~{max_p}")
            elif min_p > 0:
                price_desc_list.append(f"最低={min_p}")
            elif max_p > 0:
                price_desc_list.append(f"最高={max_p}")
            if merged:
                price_desc_list.append(f"排除线路={merged}")
            price_desc = ", ".join(price_desc_list) or "不限"

            logger.info("SmsBower %s: service=%s country=%s 金额限制=%s (尝试 %d/%d)",
                        action, service, country, price_desc, retry_idx + 1, max_price_retries)
            try:
                resp = self._request(params)
                resp_text = resp.text.strip()
                last_resp_text = resp_text
                logger.info("SmsBower %s resp: status=%s text=%s", action, resp.status_code, resp_text[:500])

                if "BANNED:" in resp_text and "providerIds" in params and not tried_operator_fallback:
                    tried_operator_fallback = True
                    op_params = {k: v for k, v in params.items() if k != "providerIds"}
                    if wanted_ops:
                        op_params["operator"] = ",".join(wanted_ops)
                    logger.warning(
                        "SmsBower: providerIds 被限制 (%s)，改用 operator=%s 重试...",
                        resp_text[:80], op_params.get("operator"),
                    )
                    try:
                        resp = self._request(op_params)
                        resp_text = resp.text.strip()
                        last_resp_text = resp_text
                        logger.info("SmsBower operator 回退 resp: %s", resp_text[:300])
                    except Exception:
                        pass

                if action == "getNumberV2":
                    try:
                        data = resp.json()
                    except ValueError:
                        data = None
                    if isinstance(data, dict) and data.get("activationId"):
                        aid = str(data.get("activationId"))
                        cost_raw = data.get("activationCost") or data.get("cost") or data.get("price")
                        op_id = str(data.get("activationOperator") or "").strip()
                        actual_cost = None
                        if cost_raw is not None:
                            try:
                                actual_cost = float(cost_raw)
                            except (ValueError, TypeError):
                                actual_cost = None
                        operator_hit = bool(wanted_ops) and op_id in wanted_ops
                        if actual_cost is not None:
                            too_expensive = max_p > 0 and actual_cost > max_p + 1e-5
                            too_cheap = (not lock_by_operator) and min_p > 0 and actual_cost < min_p - 1e-5
                            if too_expensive or (too_cheap and not operator_hit):
                                logger.warning(
                                    "SmsBower: 租到号码 %s 实际金额 %.4f 不符合目标 %s~%s (线路=%s)，秒退换号...",
                                    aid, actual_cost, min_p, max_p, op_id or "?"
                                )
                                self.cancel(aid)
                                if too_cheap and op_id and op_id not in extra_except:
                                    extra_except.append(op_id)
                                continue
                        if wanted_ops and op_id and op_id not in wanted_ops:
                            logger.warning(
                                "SmsBower: 租到号码 %s 线路=%s 不是指定线路 %s，秒退换号...",
                                aid, op_id, ",".join(wanted_ops),
                            )
                            self.cancel(aid)
                            continue
                        return data
                    if "NO_NUMBERS" in resp_text:
                        raise RuntimeError(resp_text[:200] or "empty response")
                    raise RuntimeError(resp_text[:200] or "empty response")

                if resp_text.startswith("ACCESS_NUMBER:"):
                    parts = resp_text.split(":", 2)
                    if len(parts) == 3:
                        return {
                            "activationId": parts[1],
                            "phoneNumber": parts[2],
                            "countryPhoneCode": "",
                        }
                raise RuntimeError(resp_text[:200] or "empty response")
            except Exception as e:
                if "NO_NUMBERS" in str(e) and retry_idx + 1 < max_price_retries:
                    continue
                if retry_idx + 1 < max_price_retries and ("BANNED" in str(e) or "NO_NUMBERS" in str(e)):
                    continue
                raise

        raise RuntimeError(f"未租到符合指定金额({min_p} ~ {max_p})的号码: {last_resp_text[:200] or 'empty response'}")

    @staticmethod
    def _format_phone(info: dict) -> str:
        raw = str(info.get("phoneNumber") or "").strip()
        cc = str(info.get("countryPhoneCode") or "").strip()
        if raw.startswith("+"):
            return raw
        if cc and raw.startswith(cc):
            return f"+{raw}"
        if cc:
            return f"+{cc}{raw}"
        return f"+{raw}"

    def get_number(self, *, service: str, country: str = "",
                    country_candidates: Optional[list[str]] = None) -> SmsActivation:
        """租号。支持多国家候选依次尝试（按入参顺序）。

        优化：非复用模式直接并发租号，不抢全局锁，避免并发 Worker 阻塞导致 OpenAI 会话超时 409。
        """
        service_code = str(self.default_service or service or SMS_DEFAULT_SERVICE).strip()
        if not country_candidates:
            country_candidates = [str(country or self.default_country or SMS_DEFAULT_COUNTRY).strip()]

        def _do_get():
            if self.reuse_phone_to_max:
                with _SMS_CACHE_LOCK:
                    cache = self._load_cache(service_code, country_candidates[0])
                    if cache and str(cache.get("country") or "") in country_candidates:
                        activation = SmsActivation(
                            activation_id=str(cache["activation_id"]),
                            phone_number=str(cache["phone_number"]),
                            country=str(cache.get("country") or country_candidates[0]),
                            metadata={"reused": True, "use_count": int(cache.get("use_count") or 0)},
                        )
                        self.current_activation = activation
                        return activation

            failures: list[str] = []
            last_exc: Optional[Exception] = None
            for cid in country_candidates:
                cid = str(cid).strip()
                if not cid:
                    continue
                # 锁定金额时只用 V2（响应带 activationCost）；V1 无金额字段会把更便宜的号放进来
                actions = ("getNumberV2",) if (self.exact_price > 0 or self.min_price > 0) else ("getNumberV2", "getNumber")
                for action in actions:
                    try:
                        info = self._request_number_single_action(action, service_code, cid)
                        aid = str(info.get("activationId") or "")
                        phone = self._format_phone(info)
                        if not aid or not phone.strip("+"):
                            failures.append(f"{cid}: {action} 返回信息不完整")
                            continue
                        if self.reuse_phone_to_max:
                            with _SMS_CACHE_LOCK:
                                cache = {
                                    **self._cache_identity(service_code, cid),
                                    "country": cid,
                                    "activation_id": aid,
                                    "phone_number": phone,
                                    "acquired_at": time.time(),
                                    "use_count": 0,
                                    "used_codes": set(),
                                    "reuse_stopped": False,
                                    "stop_reason": "",
                                }
                                self._save_cache(cache)
                        cost_raw = info.get("activationCost") or info.get("cost") or info.get("price")
                        try:
                            actual_cost = float(cost_raw) if cost_raw is not None else None
                        except (TypeError, ValueError):
                            actual_cost = None
                        op_id = str(info.get("activationOperator") or "").strip()
                        activation = SmsActivation(
                            activation_id=aid,
                            phone_number=phone,
                            country=cid,
                            metadata={
                                "reused": False,
                                "cost": actual_cost,
                                "operator": op_id,
                            },
                        )
                        self.current_activation = activation
                        logger.info(
                            "SmsBower 租到号 %s 国家=%s 金额=%s 线路=%s (action=%s)",
                            phone, cid, actual_cost if actual_cost is not None else "未知",
                            op_id or "未知", action,
                        )
                        return activation
                    except Exception as e:
                        msg = str(e)[:120]
                        failures.append(f"{cid}: {action}={msg}")
                        last_exc = e
                        continue

            detail = " | ".join(failures) if failures else "未知"
            raise RuntimeError(f"SmsBower 依次尝试 {len(country_candidates)} 个候选国家全失败: {detail}") from last_exc

        if self.reuse_phone_to_max:
            with _SMS_VERIFY_LOCK:
                return _do_get()
        return _do_get()

    # ---- 等 code / 状态查询 ----

    def get_status(self, activation_id: str) -> dict:
        text = self._request({"action": "getStatus", "id": activation_id}).text
        return _parse_sms_status_text(text)

    def get_status_v2(self, activation_id: str) -> dict:
        try:
            resp = self._request({"action": "getStatusV2", "id": activation_id})
            text = resp.text.strip()
            try:
                data = resp.json()
            except ValueError:
                return _parse_sms_status_text(text)
            if isinstance(data, str):
                return _parse_sms_status_text(data)
            if not isinstance(data, dict):
                return {"status": "unknown"}
            if "error" in data:
                return {"status": "error", "error": str(data.get("error"))}
            raw_status = data.get("status")
            if isinstance(raw_status, str):
                parsed = _parse_sms_status_text(raw_status)
                if parsed.get("status") != "unknown":
                    return parsed
            for channel in ("sms", "call"):
                item = data.get(channel)
                if isinstance(item, dict):
                    candidate = _make_sms_candidate(activation_id, f"getStatusV2.{channel}", item.get("code"))
                    if candidate:
                        return candidate
            return {"status": "wait_code"}
        except Exception:
            return {"status": "unknown"}

    def request_resend_sms(self, activation_id: str) -> bool:
        try:
            self._request({"action": "setStatus", "id": activation_id, "status": 3})
            return True
        except Exception:
            return False

    def wait_for_code(self, activation_id: str, *, timeout: int = 80, poll: int = 3,
                       openai_resend_interval: int = 30,
                       openai_resend_max: int = 2) -> Optional[dict]:
        """等 SMS 验证码：优先从标准 getStatus 解析 6 位数字验证码。
        超过 timeout 仍没收到 → 返回 None（由上层 cancel 换号）。
        """
        deadline = time.time() + timeout
        start = time.time()
        openai_resend_count = 0
        with _SMS_CACHE_LOCK:
            cache = _SMS_CACHE or {}
            used_codes = set(cache.get("used_codes") or [])

        while time.time() < deadline:
            for src in ("v1", "v2"):
                try:
                    if src == "v1":
                        result = self.get_status(activation_id)
                    else:
                        result = self.get_status_v2(activation_id)
                    if result.get("status") == "cancel":
                        return None
                    if result.get("status") == "ok":
                        code = str(result.get("code") or "")
                        if code and code not in used_codes:
                            return {"status": "ok", "code": code,
                                    "sms_key": result.get("sms_key") or ""}
                except Exception as e:
                    logger.debug("SmsBower status %s 失败: %s", src, e)

            elapsed = time.time() - start
            # OpenAI 端 resend：仅在明确配置了回调时触发
            expected_resend_count = min(openai_resend_max, int(elapsed // openai_resend_interval))
            if expected_resend_count > openai_resend_count and self._resend_callback:
                try:
                    self._resend_callback()
                    openai_resend_count = expected_resend_count
                    logger.info(
                        "SmsBower: 已请求 OpenAI 端 resend (第 %d/%d 次, elapsed=%ds)",
                        openai_resend_count, openai_resend_max, int(elapsed),
                    )
                    self.request_resend_sms(activation_id)
                except Exception as e:
                    logger.warning("OpenAI resend callback 失败: %s", e)

            time.sleep(poll)
        return None

    def get_code(self, activation_id: str, *, timeout: int = 180) -> str:
        # ⚠️ 不再用 cache.remaining 延长 timeout：
        # 用户给的 timeout 就是真 timeout，超时就让上层换号或换 attempt。
        # （旧逻辑会被拉到 20 分钟号码生命周期，OpenAI 端 phone-otp challenge 等不了那么久）
        candidate = self.wait_for_code(activation_id, timeout=timeout)
        self.last_code_result = candidate
        return str((candidate or {}).get("code") or "")

    # ---- 状态报告 ----

    def cancel(self, activation_id: str) -> bool:
        try:
            resp = self._request({"action": "cancelActivation", "id": activation_id})
            ok = resp.status_code == 204 or "ACCESS_CANCEL" in resp.text
        except Exception:
            ok = False
        if not ok:
            try:
                resp = self._request({"action": "setStatus", "id": activation_id, "status": 8})
                ok = "ACCESS_CANCEL" in resp.text
            except Exception:
                ok = False
        with _SMS_CACHE_LOCK:
            cache = _SMS_CACHE
            if cache and str(cache.get("activation_id")) == str(activation_id):
                self._clear_cache()
        return ok

    def report_success(self, activation_id: str) -> bool:
        with _SMS_CACHE_LOCK:
            cache = _SMS_CACHE
            should_finish = False
            should_clear = False
            if cache and str(cache.get("activation_id")) == str(activation_id):
                cache["use_count"] = int(cache.get("use_count") or 0) + 1
                if self.last_code_result and self.last_code_result.get("code"):
                    used = set(cache.get("used_codes") or [])
                    used.add(self.last_code_result["code"])
                    cache["used_codes"] = used
                remaining = SMS_PHONE_LIFETIME - (time.time() - float(cache.get("acquired_at") or 0))
                if not self.reuse_phone_to_max:
                    should_finish = True
                    should_clear = True
                    cache["reuse_stopped"] = True
                elif self.phone_success_max > 0 and int(cache["use_count"]) >= self.phone_success_max:
                    should_finish = True
                    cache["reuse_stopped"] = True
                elif remaining <= 30:
                    should_finish = True
                    should_clear = True
                    cache["reuse_stopped"] = True
                self._save_cache(cache)
                if should_clear:
                    self._clear_cache()
        try:
            if should_finish or not (cache and str(cache.get("activation_id")) == str(activation_id)):
                resp = self._request({"action": "finishActivation", "id": activation_id})
                return resp.status_code in (200, 204) or "ACCESS" in resp.text
        except Exception:
            try:
                resp = self._request({"action": "setStatus", "id": activation_id, "status": 6})
                return "ACCESS" in resp.text
            except Exception:
                return False
        return True

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        with _SMS_CACHE_LOCK:
            cache = _SMS_CACHE
            if cache and str(cache.get("activation_id")) == str(activation_id):
                if self.last_code_result and self.last_code_result.get("code"):
                    used = set(cache.get("used_codes") or [])
                    used.add(self.last_code_result["code"])
                    cache["used_codes"] = used
                self._save_cache(cache)
        if self._resend_callback:
            try:
                self._resend_callback()
            except Exception:
                pass
        self.request_resend_sms(activation_id)

    def mark_send_succeeded(self, activation_id: str) -> None:
        try:
            self._request({"action": "setStatus", "id": activation_id, "status": 1})
        except Exception:
            pass

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        # 业务侧拒了这个号 → cancel 退款（号根本没用上，不能让主人白花钱）
        cancel_ok = False
        try:
            resp = self._request({"action": "setStatus", "id": activation_id, "status": 8})
            cancel_ok = "ACCESS_CANCEL" in resp.text or resp.status_code in (200, 204)
        except Exception:
            pass
        # 简化原因显示：只保留前 80 字符
        short_reason = (reason or "未知原因")[:80]
        logger.info("SmsBower 号 activation_id=%s cancel 退款 %s (原因: %s)",
                    activation_id, "✅" if cancel_ok else "❌", short_reason)
        # 同时清掉复用缓存（避免下次注册又拿到这个被拒的号）
        with _SMS_CACHE_LOCK:
            cache = _SMS_CACHE
            if cache and str(cache.get("activation_id")) == str(activation_id):
                cache["reuse_stopped"] = True
                cache["stop_reason"] = reason or "phone rejected"
                self._save_cache(cache)
                self._clear_cache()

    def set_resend_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._resend_callback = callback

