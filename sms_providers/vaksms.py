"""Vak-SMS (https://vak-sms.com/) 接码渠道。

官方文档：https://vak-sms.com/api/vak/
协议与 sms-activate 不同：JSON + apiKey 查询参数，国家用 ISO2（th/us），
OpenAI 业务码仍是 dr。
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

import requests

from .base import (
    BaseSmsProvider,
    ConfigField,
    SmsActivation,
    note_openai_phone_resend,
    openai_phone_resend_due,
    register,
)
from .util import SMS_COUNTRY_NAMES_CN, parse_price_spec, _safe_float

logger = logging.getLogger(__name__)

VAK_BASE_URLS = (
    "https://vak-sms.com",
    "https://moresms.net",
    "https://vaksms.ru",
)

# sms-activate 数字国家 ID → vak-sms ISO2
ACTIVATE_ID_TO_ISO2 = {
    "0": "ru", "1": "ua", "2": "kz", "3": "cn", "4": "ph", "5": "mm",
    "6": "id", "7": "my", "8": "ke", "9": "tz", "10": "vn", "11": "kg",
    "12": "us", "13": "il", "14": "hk", "15": "pl", "16": "gb", "19": "ng",
    "21": "eg", "22": "in", "23": "ie", "24": "kh", "25": "la", "31": "za",
    "32": "ro", "33": "co", "36": "ca", "37": "ma", "38": "gh", "39": "ar",
    "40": "uz", "43": "de", "46": "se", "48": "nl", "50": "at", "52": "th",
    "53": "sa", "54": "mx", "55": "tw", "56": "es", "62": "tr", "63": "cz",
    "65": "pe", "66": "pk", "67": "nz", "70": "ve", "73": "br", "75": "ug",
    "78": "fr", "82": "be", "83": "bg", "84": "hu", "86": "it", "95": "ae",
    "105": "ec", "117": "pt", "129": "gr", "140": "sk", "150": "cl",
    "155": "uy", "162": "fi", "164": "lu", "171": "dk", "172": "ch",
    "173": "no", "174": "au", "185": "us", "187": "us", "188": "cn",
    "189": "kr", "191": "jp",
    "34": "ee", "44": "lt", "45": "hr", "49": "lv", "59": "si",
}

ISO2_NAMES_CN = {
    "th": "泰国", "us": "美国", "ph": "菲律宾", "vn": "越南", "id": "印度尼西亚",
    "my": "马来西亚", "in": "印度", "br": "巴西", "cl": "智利", "mx": "墨西哥",
    "gb": "英国", "de": "德国", "fr": "法国", "it": "意大利", "es": "西班牙",
    "nl": "荷兰", "pl": "波兰", "ca": "加拿大", "au": "澳大利亚", "jp": "日本",
    "kr": "韩国", "tr": "土耳其", "ng": "尼日利亚", "za": "南非", "ke": "肯尼亚",
    "co": "哥伦比亚", "ar": "阿根廷", "pe": "秘鲁", "ua": "乌克兰", "kz": "哈萨克斯坦",
    "ru": "俄罗斯", "cn": "中国", "hk": "中国香港", "tw": "中国台湾", "sg": "新加坡",
    "ae": "阿联酋", "sa": "沙特阿拉伯", "eg": "埃及", "pk": "巴基斯坦", "bd": "孟加拉国",
    "ro": "罗马尼亚", "cz": "捷克", "se": "瑞典", "at": "奥地利", "be": "比利时",
    "ch": "瑞士", "no": "挪威", "dk": "丹麦", "fi": "芬兰", "ie": "爱尔兰",
    "pt": "葡萄牙", "gr": "希腊", "hu": "匈牙利", "bg": "保加利亚", "nz": "新西兰",
    "il": "以色列", "mm": "缅甸", "kh": "柬埔寨", "la": "老挝", "uz": "乌兹别克斯坦",
    "kg": "吉尔吉斯斯坦", "ec": "厄瓜多尔", "uy": "乌拉圭", "ve": "委内瑞拉",
    "ug": "乌干达", "gh": "加纳", "ma": "摩洛哥", "tz": "坦桑尼亚", "lu": "卢森堡",
    "sk": "斯洛伐克", "si": "斯洛文尼亚", "hr": "克罗地亚", "lt": "立陶宛",
    "lv": "拉脱维亚", "ee": "爱沙尼亚", "rs": "塞尔维亚", "ba": "波黑",
    "al": "阿尔巴尼亚", "mk": "北马其顿", "md": "摩尔多瓦", "cy": "塞浦路斯",
    "is": "冰岛", "mt": "马耳他", "bd": "孟加拉国", "lk": "斯里兰卡",
}

# 拉库存时轮询的国家。必须覆盖官网能搜到的 openai.com 热门国（含斯洛文尼亚 si）。
STOCK_COUNTRIES = tuple(dict.fromkeys((
    "th", "us", "ph", "vn", "id", "my", "in", "br", "cl", "mx",
    "gb", "de", "fr", "it", "es", "nl", "pl", "ca", "au", "jp",
    "kr", "tr", "ng", "za", "ke", "co", "ar", "pe", "ua", "kz",
    "ru", "hk", "tw", "sg", "ae", "ro", "cz", "se",
    "si", "sk", "ie", "pt", "hu", "bg", "at", "be", "ch", "no",
    "dk", "fi", "gr", "nz", "il", "hr", "lt", "lv", "ee", "rs",
)))

VAK_ERRORS = {
    "apiKeyNotFound": "API Key 无效或不存在",
    "noService": "该国家没有此业务",
    "noNumber": "该国家暂无号码",
    "noMoney": "余额不足",
    "badCountry": "国家代码无效",
    "waitChange": "请稍后再试",
    "noActivation": "激活 ID 不存在",
}


def normalize_vak_country(raw: str) -> str:
    cid = str(raw or "").strip()
    if not cid:
        return "th"
    low = cid.lower()
    if low in ISO2_NAMES_CN or (len(low) == 2 and low.isalpha()):
        return low
    if cid in ACTIVATE_ID_TO_ISO2:
        return ACTIVATE_ID_TO_ISO2[cid]
    if low in ACTIVATE_ID_TO_ISO2:
        return ACTIVATE_ID_TO_ISO2[low]
    for iso, name in ISO2_NAMES_CN.items():
        if name == cid:
            return iso
    for code, name in SMS_COUNTRY_NAMES_CN.items():
        if name != cid:
            continue
        if len(code) == 2 and code.isalpha():
            return code.lower()
        mapped = ACTIVATE_ID_TO_ISO2.get(code)
        if mapped:
            return mapped
    return low if len(low) == 2 and low.isalpha() else cid


def _extract_otp6(text: str) -> str:
    m = re.search(r"(?<!\d)(\d{6})(?!\d)", str(text or ""))
    return m.group(1) if m else ""


def _fmt_vak_price(p: float) -> str:
    """官网 priceMap 的 key 是四位小数，如 0.0214 / 0.0200。"""
    return f"{float(p):.4f}"


def _fmt_vak_count(c: int) -> str:
    """和官网一样用 11.85K，不用 1.19万（看起来像差了 10 倍）。"""
    n = int(c or 0)
    if n >= 1000:
        return f"{n / 1000:.2f}".rstrip("0").rstrip(".") + "K"
    return str(n)


def _iter_live_prices(price_map: dict) -> list[tuple[float, int, str]]:
    live = []
    for raw_k, raw_c in (price_map or {}).items():
        try:
            p = float(raw_k)
            c = int(raw_c or 0)
        except (TypeError, ValueError):
            continue
        if p > 0 and c > 0:
            live.append((p, c, str(raw_k)))
    live.sort(key=lambda x: x[0])
    return live


def _resolve_vak_max_price(price_map: dict, cap: float, lock_exact: bool) -> tuple[str | None, str]:
    """把界面金额对到官网正在卖的 priceMap key。

    点选锁定时：优先原样 key / 极近档（0.0215 vs 0.0214），否则用不超过目标价的最高在售档。
    只填上限时：仍发四位小数字符串，不锁死。
    """
    live = _iter_live_prices(price_map)
    if cap <= 0:
        return None, ""
    want = _fmt_vak_price(cap)
    if not live:
        return (None, "该国暂无在售 openai.com 档位") if lock_exact else (want, "")
    for p, _c, k in live:
        if k == want or k == str(cap).strip() or abs(p - cap) <= 0.00015:
            return k, ""
    if not lock_exact:
        return want, ""
    below = [x for x in live if x[0] <= cap + 1e-9]
    if below:
        p, c, k = below[-1]
        if cap - p <= 0.002:
            return k, f"锁定档 {want} 当前无货，改用在售 {k}（余{c}）"
    shown = "、".join(f"{k}×{c}" for _p, c, k in live[:8])
    return None, f"锁定档 {want} 无货。当前在售: {shown}"


@register
class VakSmsProvider(BaseSmsProvider):
    kind = "vaksms"
    aliases = ("vak-sms", "vak_sms", "vak", "vaksms.com")
    display_name = "Vak-SMS"
    short_label = "vak-sms.com"
    description = "vak-sms.com 官方 JSON 接码。国家用 ISO2（如 th 泰国），OpenAI 业务码 dr"
    sort_order = 25
    needs_api_key = True
    uses_cdk_pool = False
    uses_country = True
    uses_price_tiers = True
    uses_provider_ids = False
    uses_reuse_phone = False
    uses_auto_country = True
    country_scheme = "iso2"
    default_country = "th"
    default_service = "dr"
    default_timeout = 80
    recommended_timeout = 80
    max_timeout = 90
    timeout_hint = "推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。"
    auto_report_success_on_code = False
    config_fields = [
        ConfigField("sms_api_key", "API Key", type="password", required=True,
                    placeholder="vak-sms.com 个人中心的 apiKey",
                    help="在 https://vak-sms.com/lk/ 复制 API Key"),
    ]

    @classmethod
    def from_config(cls, config: dict) -> "VakSmsProvider":
        api_key = str(config.get("sms_api_key") or "").strip()
        country = normalize_vak_country(config.get("sms_country") or cls.default_country)
        service = str(config.get("sms_service") or "").strip() or "dr"
        if service.lower() in ("openai", "chatgpt"):
            service = "dr"
        proxy = (str(config.get("sms_proxy") or "")).strip() or None
        price_spec = config.get("sms_price") or config.get("sms_max_price") or config.get("sms_price_spec")
        _min_p, max_p, exact_p = parse_price_spec(price_spec)
        if "sms_max_price" in config:
            max_p = _safe_float(config.get("sms_max_price"), max_p)
        if "sms_exact_price" in config:
            exact_p = _safe_float(config.get("sms_exact_price"), exact_p)
        operator = str(
            config.get("sms_operator")
            or config.get("operator")
            or ""
        ).strip()
        # 文档：maxPrice=金额上限，fixedPrice=布尔。点选档位 = maxPrice+fixedPrice=true 锁死该档。
        cap = exact_p if exact_p > 0 else max_p
        return cls(
            api_key=api_key,
            default_service=service,
            default_country=country,
            max_price=cap,
            lock_exact=exact_p > 0,
            operator=operator,
            proxy=proxy,
        )

    def __init__(
        self,
        api_key: str,
        *,
        default_service: str = "dr",
        default_country: str = "th",
        max_price: float = -1,
        lock_exact: bool = False,
        operator: str = "",
        proxy: Optional[str] = None,
    ):
        self.api_key = str(api_key or "").strip()
        self.default_service = str(default_service or "dr").strip() or "dr"
        self.default_country = normalize_vak_country(default_country)
        self.max_price = float(max_price or -1)
        self.lock_exact = bool(lock_exact)
        self.operator = str(operator or "").strip()
        self._proxy = (proxy or "").strip() or None
        self._proxies = {"http": self._proxy, "https": self._proxy} if self._proxy else None
        self._base_url = VAK_BASE_URLS[0]
        self._resend_callback: Optional[Callable[[], None]] = None
        self.current_activation: Optional[SmsActivation] = None

    def _request(self, path: str, params: Optional[dict] = None, *, needs_key: bool = True, timeout: int = 25) -> dict:
        payload = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        if needs_key:
            payload["apiKey"] = self.api_key
        last_err = None
        urls = [self._base_url] + [u for u in VAK_BASE_URLS if u != self._base_url]
        for base in urls:
            try:
                resp = requests.get(
                    f"{base}{path}",
                    params=payload,
                    timeout=timeout,
                    proxies=self._proxies,
                    headers={"Accept": "application/json"},
                )
                try:
                    data = resp.json() if resp.content else {}
                except Exception:
                    data = {}
                if not isinstance(data, dict):
                    last_err = RuntimeError(f"Vak-SMS 非 JSON 响应: {(resp.text or '')[:180]}")
                    continue
                err = str(data.get("error") or "").strip()
                if err:
                    raise RuntimeError(VAK_ERRORS.get(err, err))
                # 404 页面是 {statusCode:404,message:...}，没有 error 字段。
                # 以前当成功返回，取消号码会假成功、号一直挂到超时扣费。
                if resp.status_code >= 400:
                    msg = str(data.get("message") or data.get("code") or "").strip()
                    last_err = RuntimeError(msg or f"HTTP {resp.status_code} {path}")
                    logger.warning("Vak-SMS %s %s HTTP %s: %s", base, path, resp.status_code, last_err)
                    continue
                self._base_url = base
                return data
            except RuntimeError:
                raise
            except Exception as e:
                last_err = e
                logger.warning("Vak-SMS %s %s 失败: %s", base, path, e)
        raise RuntimeError(f"Vak-SMS 请求失败 {path}: {last_err}")

    def get_balance(self) -> float:
        if not self.api_key:
            raise RuntimeError("Vak-SMS 未配置 API Key")
        data = self._request("/api/getBalance/")
        try:
            return float(data.get("balance") or 0)
        except (TypeError, ValueError):
            raise RuntimeError(f"Vak-SMS getBalance 异常: {data}")

    def get_number(
        self,
        *,
        service: str,
        country: str = "",
        country_candidates: Optional[list[str]] = None,
    ) -> SmsActivation:
        if not self.api_key:
            raise RuntimeError("Vak-SMS 未配置 API Key")
        service_code = str(self.default_service or service or "dr").strip() or "dr"
        if service_code.lower() in ("openai", "chatgpt"):
            service_code = "dr"
        if not country_candidates:
            country_candidates = [country or self.default_country]
        mapped = []
        seen = set()
        for c in country_candidates:
            iso = normalize_vak_country(c)
            if iso and iso not in seen:
                seen.add(iso)
                mapped.append(iso)
        if not mapped:
            mapped = [self.default_country]

        last_err = None
        for iso in mapped:
            offer = {}
            try:
                offer = self._offer_blob(iso, service_code)
            except Exception as e:
                logger.debug("Vak-SMS 拉报价失败 country=%s: %s", iso, e)
            price_map = offer.get("priceMap") if isinstance(offer, dict) else {}
            live = _iter_live_prices(price_map)
            cap = self.max_price if self.max_price and self.max_price > 0 else -1
            send_price = None
            note = ""
            if cap > 0:
                send_price, note = _resolve_vak_max_price(price_map, cap, self.lock_exact)
                if self.lock_exact and not send_price:
                    last_err = RuntimeError(f"{iso}: {note}")
                    logger.warning("Vak-SMS getNumber country=%s 失败: %s", iso, last_err)
                    continue
            try:
                activation = self._buy_number(iso, service_code, send_price, self.lock_exact and bool(send_price))
                if note:
                    activation.metadata["vak_note"] = note
                return activation
            except Exception as e:
                msg = str(e)
                if "暂无号码" in msg or "nonumber" in msg.lower():
                    shown = "、".join(f"{k}×{_fmt_vak_count(c)}" for _p, c, k in live[:6]) or "无"
                    last_err = RuntimeError(
                        f"{iso}: 锁定档位 {send_price or cap or '默认'} API 无号，不会改点其它档。"
                        f"官网展示 {shown}（网页「台电脑」库存经常不能用 API 下单）。请改点有货的档，或填 <=价格。"
                    )
                else:
                    last_err = e
                logger.warning("Vak-SMS getNumber country=%s 失败: %s", iso, last_err)
                continue
        raise RuntimeError(
            f"Vak-SMS 依次尝试 {len(mapped)} 个国家全失败: {last_err}"
        ) from last_err

    def _buy_number(self, iso: str, service_code: str, send_price: str | None, lock: bool) -> SmsActivation:
        params = {
            "service": service_code,
            "country": iso,
            "rent": "false",
            "price": "true",
        }
        if self.operator and not str(self.operator).replace(".", "", 1).isdigit():
            params["operator"] = self.operator.split(",")[0].strip()
        if send_price:
            params["maxPrice"] = send_price
            if lock:
                params["fixedPrice"] = "true"
        logger.info(
            "Vak-SMS getNumber country=%s maxPrice=%s fixedPrice=%s",
            iso, params.get("maxPrice"), params.get("fixedPrice"),
        )
        data = self._request("/api/getNumber/", params)
        tel = str(data.get("tel") or data.get("number") or "").strip()
        aid = str(data.get("idNum") or data.get("id") or "").strip()
        if not tel or not aid:
            raise RuntimeError(f"{iso}: 返回缺号码 {data}")
        phone = tel if tel.startswith("+") else f"+{tel.lstrip('+')}"
        activation = SmsActivation(
            activation_id=aid,
            phone_number=phone,
            country=iso,
            metadata={
                "raw_tel": tel,
                "service": service_code,
                "cost": data.get("price"),
            },
        )
        self.current_activation = activation
        logger.info("Vak-SMS 租到号 %s 国家=%s idNum=%s price=%s", phone, iso, aid, data.get("price"))
        return activation

    def get_code(self, activation_id: str, *, timeout: int = 180, stop_check: Optional[Callable[[], bool]] = None) -> str:
        deadline = time.time() + max(15, int(timeout))
        last_sms = ""
        started = time.time()
        resend_count = 0
        while time.time() < deadline:
            if stop_check:
                try:
                    if stop_check():
                        logger.info("Vak-SMS 等待被中止 idNum=%s", activation_id)
                        return ""
                except Exception:
                    pass
            try:
                data = self._request("/api/getSmsCode/", {"idNum": activation_id})
                sms = data.get("smsCode")
                if isinstance(sms, list):
                    sms = sms[-1] if sms else None
                if sms and str(sms).lower() not in ("null", "none", ""):
                    code = _extract_otp6(str(sms)) or str(sms).strip()
                    if code:
                        logger.info("Vak-SMS 收到验证码 idNum=%s", activation_id)
                        return code
                    last_sms = str(sms)
            except Exception as e:
                logger.debug("Vak-SMS getSmsCode: %s", e)
            elapsed = int(time.time() - started)
            if openai_phone_resend_due(elapsed, resend_count) and self._resend_callback:
                logger.info(
                    "Vak-SMS 等待 %ss 未收码，触发 OpenAI 补发 %s/%s idNum=%s",
                    elapsed, resend_count + 1, 1, activation_id,
                )
                resend_count = note_openai_phone_resend(self._resend_callback, resend_count)
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            time.sleep(min(5, remaining))
        if last_sms:
            return _extract_otp6(last_sms)
        return ""

    def cancel(self, activation_id: str) -> bool:
        aid = str(activation_id or "").strip()
        if not aid:
            return False
        last_err = None
        for attempt in range(3):
            for path in ("/api/setStatus/", "/api/setStatus"):
                try:
                    data = self._request(path, {"idNum": aid, "status": "end"})
                    logger.info("Vak-SMS 取消退款 idNum=%s path=%s resp=%s", aid, path, data)
                    return True
                except Exception as e:
                    last_err = e
                    msg = str(e).lower().replace("_", "").replace(" ", "")
                    if "noactivation" in msg:
                        logger.info("Vak-SMS 激活已关闭 idNum=%s，视为取消成功", aid)
                        return True
            time.sleep(0.6 * (attempt + 1))
        logger.warning("Vak-SMS cancel idNum=%s 失败: %s", aid, last_err)
        return False

    def report_success(self, activation_id: str) -> bool:
        # 已用于 OpenAI 校验成功：不要 end（end=取消退款）。号会在平台侧到期自动结束。
        return True

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        ok = self.cancel(activation_id)
        if not ok:
            logger.warning("Vak-SMS mark_send_failed 取消未成功 idNum=%s reason=%s", activation_id, reason or "-")

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        for path in ("/api/setStatus/", "/api/setStatus"):
            try:
                self._request(path, {"idNum": activation_id, "status": "send"})
                break
            except Exception:
                continue
        if self._resend_callback:
            try:
                self._resend_callback()
            except Exception:
                pass

    def set_resend_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self._resend_callback = callback

    def _offer_blob(self, iso: str, service_code: str) -> dict:
        data = self._request(
            "/api/getOfferNumberList",
            {"country": iso},
            needs_key=bool(self.api_key),
            timeout=12,
        )
        if not isinstance(data, dict):
            return {}
        for key in (service_code, "dr", "openai.com"):
            blob = data.get(key)
            if isinstance(blob, dict) and (
                blob.get("priceMap") or blob.get("minPrice") or blob.get("totalCount")
            ):
                return blob
        return {}

    _top_cache: tuple[float, str, list] = (0.0, "", [])

    def get_top_countries(self, service: Optional[str] = None, **_kwargs) -> list[dict]:
        """国家下拉用官网报价：minPrice + 各档库存合计，不是 SmsBower 数字 ID。"""
        service_code = str(service or self.default_service or "dr").strip() or "dr"
        if service_code.lower() in ("openai", "chatgpt"):
            service_code = "dr"
        now = time.time()
        cache_t, cache_svc, cache_rows = type(self)._top_cache
        if cache_rows and cache_svc == service_code and now - cache_t < 45:
            return cache_rows
        rows: list[dict] = []

        def _one(iso: str) -> Optional[dict]:
            try:
                blob = self._offer_blob(iso, service_code)
                price_map = blob.get("priceMap") if isinstance(blob.get("priceMap"), dict) else {}
                live = []
                for raw_price, raw_count in price_map.items():
                    try:
                        p = float(raw_price)
                        c = int(raw_count or 0)
                    except (TypeError, ValueError):
                        continue
                    if p > 0 and c > 0:
                        live.append((p, c))
                if live:
                    live.sort(key=lambda x: x[0])
                    min_price = live[0][0]
                    total = sum(c for _, c in live)
                else:
                    try:
                        min_price = float(blob.get("minPrice") or 0) or None
                    except (TypeError, ValueError):
                        min_price = None
                    try:
                        total = int(blob.get("totalCount") or 0)
                    except (TypeError, ValueError):
                        total = 0
                    if min_price is None and total <= 0:
                        return None
                return {
                    "country": iso,
                    "price": min_price,
                    "count": total,
                    "min_price": min_price,
                }
            except Exception as e:
                logger.debug("Vak-SMS 报价 %s 失败: %s", iso, e)
                return None

        with ThreadPoolExecutor(max_workers=12) as pool:
            futs = {pool.submit(_one, iso): iso for iso in STOCK_COUNTRIES}
            for fut in as_completed(futs):
                row = fut.result()
                if row:
                    rows.append(row)
        rows.sort(key=lambda r: (
            0 if r.get("country") == "th" else 1,
            -(int(r.get("count") or 0) > 0),
            r.get("price") if r.get("price") is not None else 999,
            -(r.get("count") or 0),
        ))
        type(self)._top_cache = (now, service_code, rows)
        return rows

    def get_country_price_tiers(self, country: str, service: Optional[str] = None) -> list[dict]:
        """拉官网同款多档价格（getOfferNumberList.priceMap），点选即锁定该价。"""
        iso = normalize_vak_country(country or self.default_country)
        service_code = str(service or self.default_service or "dr").strip() or "dr"
        if service_code.lower() in ("openai", "chatgpt"):
            service_code = "dr"
        try:
            blob = self._offer_blob(iso, service_code)
        except Exception as e:
            logger.warning("Vak-SMS getOfferNumberList country=%s 失败: %s", iso, e)
            return []
        if not blob:
            return []
        price_map = blob.get("priceMap") if isinstance(blob.get("priceMap"), dict) else {}
        tiers = []
        for raw_price, raw_count in price_map.items():
            try:
                p = float(raw_price)
                c = int(raw_count or 0)
            except (TypeError, ValueError):
                continue
            if c <= 0 or p <= 0:
                continue
            price_key = str(raw_price)
            price_str = f"{p:.4f}".rstrip("0").rstrip(".")
            c_str = _fmt_vak_count(c)
            tiers.append({
                "id": price_key,
                "provider_id": "",
                "price": p,
                "price_key": price_key,
                "price_str": price_str,
                "count": c,
                "label": f"{price_str}$ · {c_str}",
                "tag_label": f"{price_str}$ · {c_str}",
            })
        tiers.sort(key=lambda x: (x["price"], -x["count"]))
        return tiers

    def get_best_country(self, service: Optional[str] = None, *,
                         min_stock: int = 20, max_price: float = 0,
                         strict_whitelist: bool = False,
                         allowed_countries: Optional[list[str]] = None) -> Optional[str]:
        try:
            rows = self.get_top_countries(service=service)
        except Exception as e:
            logger.warning("Vak-SMS get_best_country 失败: %s", e)
            return None
        allowed = None
        if allowed_countries:
            allowed = {normalize_vak_country(c) for c in allowed_countries if c}
        whitelist = {"th"} if strict_whitelist else None
        for row in rows:
            iso = str(row.get("country") or "")
            if allowed is not None and iso not in allowed:
                continue
            if whitelist is not None and iso not in whitelist:
                continue
            count = int(row.get("count") or 0)
            price = float(row.get("price") or 0)
            if count < min_stock:
                continue
            if max_price > 0 and price > max_price:
                continue
            return iso
        for row in rows:
            iso = str(row.get("country") or "")
            if allowed is not None and iso not in allowed:
                continue
            if int(row.get("count") or 0) > 0:
                return iso
        return None
