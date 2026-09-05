"""SMS 接码 provider 抽象 + SmsBower 实现。

设计参考：asz798838958/GeniusFKoai 的 core/base_sms.py，但裁剪掉浏览器回调相关代码、
仅保留纯协议注册需要的两段流程：
    1) rent number    → provider.get_number(service=..., country=...)
    2) wait sms code  → provider.get_code(activation_id, timeout=...)
    3) 成功/失败       → provider.report_success / cancel / mark_code_failed

⚠️ 关键事实：OpenAI 自 2025 年起对大部分国家改用 WhatsApp 验证，**纯 SMS 路径目前只有
泰国（country_id=52）确认可用**。其它国家可能抽到 WhatsApp 号导致拿不到 SMS。
SmsBower 的 `auto_select_country=True` 会按价格 + 库存自动选号。
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import requests

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 抽象基类
# ---------------------------------------------------------------------------


@dataclass
class SmsActivation:
    """一次手机号租用的句柄。"""
    activation_id: str
    phone_number: str          # E.164 格式，带 + 前缀
    country: str = ""
    metadata: dict = field(default_factory=dict)


class BaseSmsProvider(ABC):
    """接码 provider 抽象基类。"""

    auto_report_success_on_code = True  # True = 收到 code 即报成功；False = 等业务侧确认

    @abstractmethod
    def get_number(self, *, service: str, country: str = "",
                    country_candidates: Optional[list[str]] = None) -> SmsActivation:
        ...

    @abstractmethod
    def get_code(self, activation_id: str, *, timeout: int = 180) -> str:
        ...

    @abstractmethod
    def cancel(self, activation_id: str) -> bool:
        ...

    def get_balance(self) -> float:
        """查询余额（货币随平台）。"""
        raise NotImplementedError

    def report_success(self, activation_id: str) -> bool:
        """业务侧验证通过后调用，平台可能据此结算/允许复用。"""
        return True

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        """业务侧收到 code 但 validate 失败 → 请求 resend。"""
        return None

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        """业务侧拒绝该手机号（add-phone/send 返错）→ 停止复用。"""
        return None

    def mark_send_succeeded(self, activation_id: str) -> None:
        """业务侧已成功触发短信发送（add-phone/send 200）。"""
        return None

    def set_resend_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """注册 resend 钩子（SmsBower 长等待时回调业务侧重新触发 OTP）。"""
        return None


# ---------------------------------------------------------------------------
# 国家 ID → 中文名映射（sms-activate.org 协议系，SmsBower 共用）
# ---------------------------------------------------------------------------

SMS_COUNTRY_NAMES_CN: dict[str, str] = {
    "0": "俄罗斯", "1": "乌克兰", "2": "哈萨克斯坦", "3": "中国", "4": "菲律宾",
    "5": "缅甸", "6": "印度尼西亚", "7": "马来西亚", "8": "肯尼亚", "9": "坦桑尼亚",
    "10": "越南", "11": "吉尔吉斯斯坦", "12": "美国(虚拟)", "13": "以色列", "14": "香港",
    "15": "波兰", "16": "英国", "17": "马达加斯加", "18": "刚果(布)", "19": "尼日利亚",
    "20": "澳门", "21": "埃及", "22": "印度", "23": "爱尔兰", "24": "柬埔寨",
    "25": "老挝", "26": "海地", "27": "科特迪瓦", "28": "冈比亚", "29": "塞尔维亚",
    "30": "也门", "31": "南非", "32": "罗马尼亚", "33": "哥伦比亚", "34": "爱沙尼亚",
    "35": "阿塞拜疆", "36": "加拿大", "37": "摩洛哥", "38": "加纳", "39": "阿根廷",
    "40": "乌兹别克斯坦", "41": "喀麦隆", "42": "乍得", "43": "德国", "44": "立陶宛",
    "45": "克罗地亚", "46": "瑞典", "47": "伊拉克", "48": "荷兰", "49": "拉脱维亚",
    "50": "奥地利", "51": "白俄罗斯", "52": "泰国", "53": "沙特阿拉伯", "54": "墨西哥",
    "55": "台湾", "56": "西班牙", "57": "伊朗", "58": "阿尔及利亚", "59": "斯洛文尼亚",
    "60": "孟加拉国", "61": "塞内加尔", "62": "土耳其", "63": "捷克", "64": "斯里兰卡",
    "65": "秘鲁", "66": "巴基斯坦", "67": "新西兰", "68": "几内亚", "69": "马里",
    "70": "委内瑞拉", "71": "埃塞俄比亚", "72": "蒙古", "73": "巴西", "74": "阿富汗",
    "75": "乌干达", "76": "安哥拉", "77": "塞浦路斯", "78": "法国", "79": "巴布亚新几内亚",
    "80": "莫桑比克", "81": "尼泊尔", "82": "比利时", "83": "保加利亚", "84": "匈牙利",
    "85": "摩尔多瓦", "86": "意大利", "87": "巴拉圭", "88": "洪都拉斯", "89": "突尼斯",
    "90": "尼加拉瓜", "91": "东帝汶", "92": "玻利维亚", "93": "哥斯达黎加", "94": "危地马拉",
    "95": "阿联酋", "96": "津巴布韦", "97": "波多黎各", "98": "苏丹", "99": "多哥",
    "100": "科威特", "101": "萨尔瓦多", "102": "利比亚", "103": "牙买加", "104": "特立尼达和多巴哥",
    "105": "厄瓜多尔", "106": "斯威士兰", "107": "阿曼", "108": "波黑", "109": "多米尼加",
    "110": "叙利亚", "111": "卡塔尔", "112": "巴拿马", "113": "古巴", "114": "毛里塔尼亚",
    "115": "塞拉利昂", "116": "约旦", "117": "葡萄牙", "118": "巴巴多斯", "119": "布隆迪",
    "120": "贝宁", "121": "文莱", "122": "巴哈马", "123": "博茨瓦纳", "124": "伯利兹",
    "125": "中非", "126": "多米尼克", "127": "格林纳达", "128": "格鲁吉亚", "129": "希腊",
    "130": "几内亚比绍", "131": "圭亚那", "132": "冰岛", "133": "科摩罗", "134": "利比里亚",
    "135": "莱索托", "136": "马拉维", "137": "纳米比亚", "138": "尼日尔", "139": "卢旺达",
    "140": "斯洛伐克", "141": "苏里南", "142": "塔吉克斯坦", "143": "摩纳哥", "144": "巴林",
    "145": "留尼汪岛", "146": "赞比亚", "147": "亚美尼亚", "148": "索马里", "149": "刚果(金)",
    "150": "智利", "151": "布基纳法索", "152": "黎巴嫩", "153": "加蓬", "154": "阿尔巴尼亚",
    "155": "乌拉圭", "156": "毛里求斯", "157": "不丹", "158": "马尔代夫", "159": "瓜德罗普岛",
    "160": "土库曼斯坦", "161": "法属圭亚那", "162": "芬兰", "163": "圣卢西亚", "164": "卢森堡",
    "165": "圣文森特", "166": "赤道几内亚", "167": "吉布提", "168": "安提瓜和巴布达", "169": "开曼群岛",
    "170": "黑山", "171": "丹麦", "172": "瑞士", "173": "挪威", "174": "澳大利亚",
    "175": "厄立特里亚", "176": "南苏丹", "177": "圣多美", "178": "阿鲁巴岛", "179": "蒙特塞拉特",
    "180": "安圭拉岛", "181": "北马其顿", "182": "塞舌尔", "183": "新喀里多尼亚", "184": "佛得角",
    "185": "美国(实体)", "186": "巴勒斯坦", "187": "美国", "188": "中国", "189": "韩国",
    "190": "科特迪瓦", "191": "日本",
}


def country_label(country_id) -> str:
    """返回 '52 泰国' 这样的展示标签。"""
    cid = str(country_id or "").strip()
    name = SMS_COUNTRY_NAMES_CN.get(cid, "")
    return f"{cid} {name}".strip()


# ---------------------------------------------------------------------------
# SmsBower / SMSBower —— 共享 API 协议
# ---------------------------------------------------------------------------

SMS_DEFAULT_SERVICE = "dr"
SMS_DEFAULT_COUNTRY = "52"  # Thailand —— OpenAI 走 SMS 的稳定国家
SMS_PHONE_LIFETIME = 20 * 60  # 号码租用窗口（秒）
_SMS_CACHE_LOCK = threading.Lock()
_SMS_VERIFY_LOCK = threading.RLock()
_SMS_CACHE: Optional[dict] = None  # 跨线程共享的号码复用缓存

# OpenAI 走纯 SMS 的国家白名单（截至 2025-2026 实测；其它国家会抽到 WhatsApp 号）
OPENAI_SMS_COUNTRIES = {"52"}  # Thailand only


def _hash_secret(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _safe_bool(value, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "否"}


def _project_cache_dir() -> Path:
    root = Path(__file__).resolve().parent
    cache = root / "data"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _smsbower_cache_file() -> Path:
    return _project_cache_dir() / ".smsbower_phone_cache.json"


def parse_price_spec(spec) -> tuple[float, float, float]:
    """解析价格字符串或数值配置。
    返回 (min_price, max_price, exact_price)
    - 留空 / -1 / 0 -> (-1.0, -1.0, -1.0) 不限
    - "0.008" 或 "=0.008" -> (0.008, 0.008, 0.008) 锁定指定金额
    - "0.008-0.01" 或 "0.008~0.01" -> (0.008, 0.01, -1.0) 价格区间
    - ">=0.008" 或 ">0.008" -> (0.008, -1.0, -1.0) 最低金额限制
    - "<=0.25" 或 "<0.25" -> (-1.0, 0.25, -1.0) 最高金额限制
    """
    if spec is None or spec == "":
        return -1.0, -1.0, -1.0
    if isinstance(spec, (int, float)):
        val = float(spec)
        if val <= 0:
            return -1.0, -1.0, -1.0
        return val, val, val
    s = str(spec).strip()
    if not s or s.lower() in ("-1", "0", "不限", "none", "null"):
        return -1.0, -1.0, -1.0

    # 区间格式: 0.008-0.01 / 0.008~0.01 / 0.008..0.01 / 0.008,0.01
    for sep in ("..", "-", "~", ","):
        if sep in s:
            parts = s.split(sep, 1)
            try:
                min_p = float(parts[0].strip()) if parts[0].strip() else -1.0
            except ValueError:
                min_p = -1.0
            try:
                max_p = float(parts[1].strip()) if parts[1].strip() else -1.0
            except ValueError:
                max_p = -1.0
            exact_p = min_p if (min_p > 0 and min_p == max_p) else -1.0
            return min_p, max_p, exact_p

    # >= / >
    if s.startswith(">="):
        try:
            return float(s[2:].strip()), -1.0, -1.0
        except ValueError:
            return -1.0, -1.0, -1.0
    if s.startswith(">"):
        try:
            return float(s[1:].strip()), -1.0, -1.0
        except ValueError:
            return -1.0, -1.0, -1.0

    # <= / <
    if s.startswith("<="):
        try:
            return -1.0, float(s[2:].strip()), -1.0
        except ValueError:
            return -1.0, -1.0, -1.0
    if s.startswith("<"):
        try:
            return -1.0, float(s[1:].strip()), -1.0
        except ValueError:
            return -1.0, -1.0, -1.0

    # = / ==
    if s.startswith("=="):
        try:
            v = float(s[2:].strip())
            return v, v, v
        except ValueError:
            return -1.0, -1.0, -1.0
    if s.startswith("="):
        try:
            v = float(s[1:].strip())
            return v, v, v
        except ValueError:
            return -1.0, -1.0, -1.0

    # 单纯数值: 如 "0.008" 或 "0.01" —— 与网页点选一致：锁死该档位，不允许更便宜
    try:
        val = float(s)
        if val <= 0:
            return -1.0, -1.0, -1.0
        return val, val, val
    except ValueError:
        return -1.0, -1.0, -1.0


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


class SmsBowerProvider(BaseSmsProvider):
    """sms-activate 协议系 provider（SmsBower / HeroSMS 共用）。"""

    DEFAULT_BASE_URL = "https://smsbower.page/stubs/handler_api.php"
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
        """按价格 + 库存排序返回国家列表。"""
        service_code = str(service or self.default_service or SMS_DEFAULT_SERVICE).strip()
        # 策略1：使用专用排名 API
        for action in ("getTopCountriesByServiceRank", "getTopCountriesByService"):
            try:
                data = self._request({"action": action, "service": service_code}).json()
                rows = self._parse_top_countries(data)
                if rows:
                    rows.sort(key=lambda r: (r.get("price") or 999, -(r.get("count") or 0)))
                    return rows
            except Exception:
                continue
        # 策略2：从 getPrices 解析
        try:
            prices = self.get_prices(service=service_code)
            rows = []
            for country_id, services in prices.items():
                if not isinstance(services, dict):
                    continue
                svc = services.get(service_code)
                if not isinstance(svc, dict):
                    continue
                price = svc.get("cost") or svc.get("price")
                count = svc.get("count") or svc.get("qty") or svc.get("available") or 0
                try:
                    price = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price = None
                try:
                    count = int(count) if count is not None else 0
                except (TypeError, ValueError):
                    count = 0
                if price is not None and count > 0:
                    rows.append({"country": str(country_id), "price": price, "count": count})
            rows.sort(key=lambda r: (r.get("price") or 999, -(r.get("count") or 0)))
            return rows
        except Exception:
            return []

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
        """查询指定国家和业务在 SmsBower 的所有可用供应商线路ID、金额与实时库存（优先 getPricesV3）。"""
        service_code = str(service or self.default_service or "dr").strip()
        country_id = str(country or self.default_country or "6").strip()
        try:
            # 优先尝试 getPricesV3 获取完整 provider_id / 价格 / 数量
            resp = self._request({
                "action": "getPricesV3",
                "service": service_code,
                "country": country_id,
            })
            if resp.status_code == 200 and resp.text.startswith("{"):
                data = resp.json()
                country_dict = data.get(country_id, {}) if isinstance(data, dict) else {}
                service_dict = country_dict.get(service_code, {}) if isinstance(country_dict, dict) else {}
                if isinstance(service_dict, dict) and service_dict:
                    tiers = []
                    for k, item in service_dict.items():
                        if not isinstance(item, dict):
                            continue
                        try:
                            pid = str(item.get("provider_id") or item.get("id") or k).strip()
                            p = float(item.get("price") or 0)
                            c = int(item.get("count") or 0)
                            if c > 0 and pid:
                                c_str = f"{c}件" if c < 10000 else f"{round(c/10000, 2)}万件"
                                tiers.append({
                                    "id": pid,
                                    "provider_id": pid,
                                    "price": p,
                                    "price_str": str(item.get("price")),
                                    "count": c,
                                    "label": f"{pid} · {p} $ (余 {c_str})",
                                    "tag_label": f"{p} $ (余 {c_str})",
                                })
                        except (ValueError, TypeError):
                            continue
                    if tiers:
                        tiers.sort(key=lambda x: (x["price"], -x["count"]))
                        return tiers

            # 备用 getPricesV2
            resp = self._request({
                "action": "getPricesV2",
                "service": service_code,
                "country": country_id,
            })
            if resp.status_code == 200 and resp.text.startswith("{"):
                data = resp.json()
                country_dict = data.get(country_id, {}) if isinstance(data, dict) else {}
                service_dict = country_dict.get(service_code, {}) if isinstance(country_dict, dict) else {}
                if isinstance(service_dict, dict):
                    tiers = []
                    for price_str, count_val in service_dict.items():
                        try:
                            p = float(price_str)
                            c = int(count_val)
                            if c > 0:
                                c_str = f"{c}件" if c < 10000 else f"{round(c/10000, 2)}万件"
                                tiers.append({
                                    "id": "",
                                    "provider_id": "",
                                    "price": p,
                                    "price_str": str(price_str),
                                    "count": c,
                                    "label": f"{price_str} $ (余 {c_str})",
                                    "tag_label": f"{price_str} $ (余 {c_str})",
                                })
                        except (ValueError, TypeError):
                            continue
                    tiers.sort(key=lambda x: x["price"])
                    return tiers
        except Exception as exc:
            logger.warning(f"SmsBower get_country_price_tiers 查询失败 (country={country_id}): {exc}")
        return []

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


# ---------------------------------------------------------------------------
# ndk.cc.cd / 鲁班接码 (LubanSMS) 卡密兑换接码 Provider
# ---------------------------------------------------------------------------


class CdkSmsProvider(BaseSmsProvider):
    """ndk.cc.cd / 鲁班接码 (LubanSMS) 卡密兑换接码 Provider。

    支持单卡密、多卡密及全自动【CDK号池】调度：
    - 无需固定 API Key，自动从 SQLite 号池 (sms_cdk_pool) 申领可用卡密
    - 针对支持多次接码的 CDK，成功接码后自动累计次数并持久保持可用状态，绝不提前废弃
    - 当平台返回 409(到期/取消) 或 422(无效) 时自动作废坏卡并轮换下一个可用卡密
    - 号池耗尽时精准报错阻断，提醒主人导入新卡密
    """

    auto_report_success_on_code = True

    def __init__(
        self,
        api_key: str = "",
        base_url: str = "https://ndk.cc.cd",
        proxy: Optional[str] = None,
    ):
        raw_keys = [k.strip().upper() for k in re.split(r"[\r\n,;]+", str(api_key or "")) if k.strip()]
        valid_cdks = []
        for k in raw_keys:
            if len(k) == 32 and "-" not in k and not k.startswith("SMS"):
                logger.info(f"[CdkSms] 忽略非卡密格式的普通 API Key: {k[:6]}***，将优先使用数据库号池卡密")
                continue
            valid_cdks.append(k)
        self.cdk_list = valid_cdks
        self._current_cdk = self.cdk_list[0] if self.cdk_list else ""
        self._cdk_idx = 0
        self.base_url = (base_url or "https://ndk.cc.cd").rstrip("/")
        self.proxy = proxy
        self.log_fn: Optional[Callable[[str], None]] = None
        self._resend_callback: Optional[Callable[[], None]] = None
        self._info_cache: dict = {}
        self._recorded_activations: set = set()
        self._lock = threading.Lock()

    def set_resend_callback(self, callback: Optional[Callable[[], None]]) -> None:
        """注册 resend 钩子（等待超时且未到码时主动触发 OpenAI 重新补发短信）。"""
        self._resend_callback = callback

    def _log(self, msg: str) -> None:
        logger.info(f"[CdkSms] {msg}")
        if callable(getattr(self, "log_fn", None)):
            try:
                self.log_fn(msg)
            except Exception:
                pass

    def _acquire_cdk(self) -> str:
        """获取一个可用 CDK（优先从显式列表，缺省全自动从数据库号池申领）。"""
        with self._lock:
            # 1. 若初始化时传入了显式静态卡密列表，轮询使用
            if self.cdk_list:
                cdk = self.cdk_list[self._cdk_idx % len(self.cdk_list)]
                self._cdk_idx += 1
                self._current_cdk = cdk
                self._log(f"🎟️ 使用显式传入卡密: [{cdk}]")
                return cdk

            # 2. 从数据库号池中动态申领可用卡密 (支持单次与多次长期卡密)
            try:
                import webui.db as db
                item = db.claim_sms_cdk()
                if item and item.get("cdk"):
                    cdk = str(item["cdk"]).strip().upper()
                    self._current_cdk = cdk
                    max_u = item.get('max_use_count', 0)
                    limit_str = '不限次(多次卡)' if max_u == 0 else f'{max_u}次'
                    self._log(
                        f"🎟️ 成功从号池申领可用卡密: [{cdk}] (已接码: {item.get('use_count', 0)}次, 上限: {limit_str})"
                    )
                    return cdk
            except Exception as e:
                logger.warning(f"[CdkSms] 从数据库号池获取卡密异常: {e}")

            # 3. 号池彻底耗尽时，抛出明确告警阻断
            raise RuntimeError(
                "【CDK号池告警】当前号池中已无可用 CDK 卡密！所有卡密均已达到使用上限或已过期，请前往【接码设置 - CDK号池管理】批量导入新卡密后再继续注册。"
            )

    def _http_post(self, path: str, payload: dict, timeout: int = 15, max_retries: int = 2) -> dict:
        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Accept": "application/json",
        }
        proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
        cdk = payload.get("code") or self._current_cdk

        for attempt in range(max_retries + 1):
            try:
                resp = requests.post(url, json=payload, headers=headers, proxies=proxies, timeout=timeout)
            except Exception as e:
                if attempt < max_retries:
                    time.sleep(1.5)
                    continue
                raise RuntimeError(f"连接 CDK 平台失败 ({self.base_url}): {e}")

            if resp.status_code == 429:
                if attempt < max_retries:
                    self._log(f"⚠️ 卡密 [{cdk}] 遭遇平台频控 429 (操作受限)，等待 2.5 秒后自动重试...")
                    time.sleep(2.5)
                    continue
                raise RuntimeError("CDK平台频控 (429): 操作受限，请勿频繁请求")

            if resp.status_code == 422:
                detail = ""
                try:
                    detail = str(resp.json().get("detail", ""))
                except Exception:
                    pass
                # 只有明确是不存在、无效或已被他人完全使用才报废
                is_real_bad = any(k in detail for k in ("不存在", "无效", "已被使用", "已核销", "封禁", "格式错误"))
                if not is_real_bad and attempt < max_retries:
                    # 类似 "兑换暂时无法完成，请稍后重试"，属于上游通道暂时繁忙，重试即可，切勿作废卡密！
                    self._log(f"⚠️ 卡密 [{cdk}] 上游通道暂时繁忙 (422: {detail})，等待 2 秒后自动重试...")
                    time.sleep(2.0)
                    continue

                msg = f"CDK兑换提示 (422): {detail or '卡密无效或兑换暂不可用'}"
                if cdk and is_real_bad:
                    try:
                        import webui.db as db
                        db.discard_sms_cdk(cdk, reason=msg, is_expired=True)
                    except Exception:
                        pass
                raise RuntimeError(msg)

            if resp.status_code == 409:
                detail = ""
                try:
                    detail = str(resp.json().get("detail", ""))
                except Exception:
                    pass
                msg = f"CDK状态提示 (409): {detail or '上游订单已自动取消或卡密已到期'}"
                if cdk:
                    try:
                        import webui.db as db
                        db.discard_sms_cdk(cdk, reason=msg, is_expired=True)
                    except Exception:
                        pass
                raise RuntimeError(msg)

            if resp.status_code >= 400:
                raise RuntimeError(f"CDK平台返回异常 HTTP {resp.status_code}: {resp.text[:180]}")

            return resp.json()

    def get_number(self, *, service: str = "", country: str = "",
                   country_candidates: Optional[list[str]] = None) -> SmsActivation:
        # 支持在卡密作废或到期时自动重试申领下一个可用卡密（最多重试 3 张）
        last_exc = None
        for attempt in range(3):
            try:
                cdk = self._acquire_cdk()
                data = self._http_post("/api/v2/public/redeem", {"code": cdk})
                self._info_cache[cdk] = data

                # 如果上游显示已取消，且允许换号，尝试主动换号
                if data.get("upstream_cancelled"):
                    logger.info(f"[CdkSms] 卡密 {cdk} 上游订单已取消，正在调用 change-number 换新号...")
                    try:
                        data = self._http_post("/api/v2/public/change-number", {"code": cdk})
                        self._info_cache[cdk] = data
                    except Exception as e:
                        logger.warning(f"[CdkSms] 换号异常: {e}")

                raw_phone = str(data.get("phone_number") or "").strip()
                if not raw_phone:
                    delivery_kind = data.get("delivery_kind")
                    if delivery_kind == "content":
                        raise RuntimeError(f"该 CDK 并非手机号产品 (类型: {delivery_kind}): {data.get('delivery_content')}")
                    raise RuntimeError(f"CDK {cdk} 未能获取到分配的手机号码: {data}")

                phone = "+" + raw_phone if not raw_phone.startswith("+") else raw_phone
                region = str(data.get("region_label") or "")
                expiry = str(data.get("expiry_label") or "")

                # 同步更新号池元数据
                try:
                    import webui.db as db
                    db.update_sms_cdk_meta(cdk, phone_number=phone, region_label=region, expiry_label=expiry)
                except Exception:
                    pass

                meta = {
                    "project_name": data.get("project_name", ""),
                    "service_label": data.get("service_label", ""),
                    "region_label": region,
                    "expiry_label": expiry,
                    "number_changes_used": data.get("number_changes_used", 0),
                    "number_changes_limit": data.get("number_changes_limit", 20),
                    "cdk": cdk,
                }
                rem_changes = meta["number_changes_limit"] - meta["number_changes_used"]
                logger.info(f"[CdkSms] ✅ 成功通过 CDK 兑换号码: {phone} (项目: {meta['project_name']}, 地区: {region}, 剩余换号: {rem_changes}次)")
                activation = SmsActivation(
                    activation_id=cdk,
                    phone_number=phone,
                    country=region,
                    metadata=meta,
                )
                self.current_activation = activation
                return activation
            except Exception as e:
                last_exc = e
                logger.warning(f"[CdkSms] 尝试兑换卡密失败 (第 {attempt + 1}/3 次): {e}")
                if "已无可用" in str(e):
                    raise
                time.sleep(1)

        raise RuntimeError(f"CDK 换取手机号连续失败: {last_exc}")

    def get_code(self, activation_id: str, *, timeout: int = 180) -> str:
        cdk = activation_id or self._current_cdk
        start_t = time.time()
        self._log(f"⏳ 正在为 CDK [{cdk}] 轮询短信验证码 (单号等待上限: {timeout}s, 系统每 3s 自动同步)...")
        poll_count = 0
        last_log_t = start_t
        elapsed = 0
        resend_count = 0
        while time.time() - start_t < timeout:
            poll_count += 1
            try:
                msgs = self._http_post("/api/v2/public/messages", {"code": cdk}, timeout=10)
                if isinstance(msgs, list) and len(msgs) > 0:
                    for m in msgs:
                        code = str(m.get("code") or "").strip()
                        if not code and m.get("text"):
                            match = re.search(r"\b(\d{6})\b", m["text"])
                            if match:
                                code = match.group(1)
                        if code:
                            self._log(f"📥 成功捕获短信验证码: {code} (耗时: {int(time.time() - start_t)}s, 消息ID: {m.get('id')})")
                            self.last_code_result = {"code": code, "id": m.get("id")}
                            # 捕获验证码成功，自动调用成功记账 (多次卡密绝不提前置为已用)
                            self._mark_success(cdk)
                            return code
            except Exception as e:
                logger.debug(f"[CdkSms] 获取短信轮询异常: {e}")

            elapsed = int(time.time() - start_t)
            remain = max(0, timeout - elapsed)

            # 每隔 20 秒且未收码时，自动联动 OpenAI 端触发补发 (最多补发 2 次，在 ~18s、~38s 各触发一次)
            expected_resends = min(2, int(elapsed // 20))
            if expected_resends > resend_count and callable(getattr(self, "_resend_callback", None)):
                resend_count = expected_resends
                try:
                    self._log(f"🔁 等待已达 {elapsed}s 未收码，正在通知 OpenAI 触发第 {resend_count} 次补发 (resend)...")
                    self._resend_callback()
                except Exception as e:
                    logger.debug(f"[CdkSms] 调用 resend_callback 异常: {e}")

            # 每 3 秒同步汇报一次实时进度，向用户明确展示倒计时与轮询情况
            if time.time() - last_log_t >= 3.0 and remain > 0:
                last_log_t = time.time()
                self._log(f"⏳ 正在同步短信验证码 (已等 {elapsed}s / 剩余 {remain}s, 轮询第 {poll_count} 次)...")

            time.sleep(3.0)

        elapsed = int(time.time() - start_t)
        self._log(f"⏱️ 该号码已达到等待上限 ({elapsed}s)，未收到短信，立即极速申请更换新号码...")
        return ""

    def _mark_success(self, cdk: str) -> None:
        """记录接码成功并安全流转号池状态 (防止重复累计与提前废弃)。"""
        if not cdk or cdk in self._recorded_activations:
            return
        self._recorded_activations.add(cdk)
        try:
            import webui.db as db
            cache_data = self._info_cache.get(cdk) or {}
            phone = str(cache_data.get("phone_number") or "")
            region = str(cache_data.get("region_label") or "")
            expiry = str(cache_data.get("expiry_label") or "")
            db.record_sms_cdk_success(cdk, phone_number=phone, region_label=region, expiry_label=expiry)
        except Exception as e:
            logger.warning(f"[CdkSms] 记录接码成功状态异常: {e}")

    def report_success(self, activation_id: str) -> bool:
        """上层完成账号注册流程后调用，确认接码成功。"""
        cdk = activation_id or self._current_cdk
        self._mark_success(cdk)
        return True

    def cancel(self, activation_id: str) -> bool:
        """取消当前号码并申请换号。"""
        cdk = activation_id or self._current_cdk
        try:
            res = self._http_post("/api/v2/public/change-number", {"code": cdk}, timeout=10)
            self._info_cache[cdk] = res
            new_phone = res.get("phone_number")
            logger.info(f"[CdkSms] 🔄 已为卡密 {cdk} 申请换号: 新号码={new_phone}")
            if new_phone:
                try:
                    import webui.db as db
                    db.update_sms_cdk_meta(cdk, phone_number=new_phone)
                except Exception:
                    pass
            return True
        except Exception as e:
            logger.warning(f"[CdkSms] 换号失败: {e}")
            return False

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        """业务侧拒绝该手机号（如被 OpenAI 判定已被注册/被风控），自动申请换新号码"""
        logger.info(f"[CdkSms] 号码被业务侧拒绝 ({reason})，自动为卡密申请换号...")
        self.cancel(activation_id)

    def get_balance(self) -> float:
        try:
            cdk = self._current_cdk
            if not cdk:
                import webui.db as db
                item = db.claim_sms_cdk()
                cdk = item["cdk"] if item else ""
            if not cdk:
                return 0.0
            data = self._http_post("/api/v2/public/redeem", {"code": cdk}, timeout=10)
            used = data.get("number_changes_used", 0)
            limit = data.get("number_changes_limit", 20)
            return float(max(0, limit - used))
        except Exception:
            return 0.0

    def get_detail_status(self) -> dict:
        try:
            import webui.db as db
            pool_stats = db.get_sms_cdk_pool_stats()
        except Exception:
            pool_stats = {}

        cdk = self._current_cdk
        if not cdk:
            try:
                import webui.db as db
                item = db.claim_sms_cdk()
                cdk = item["cdk"] if item else ""
            except Exception:
                cdk = ""

        if not cdk:
            return {
                "message": f"【CDK号池空】当前号池可用卡密: 0 张 (总卡密: {pool_stats.get('total', 0)}张)，请批量导入新卡密！",
                "phone_number": "",
                "region_label": "",
                "project_name": "",
                "expiry_label": "",
                "remaining_changes": 0,
                "pool_stats": pool_stats,
            }

        try:
            data = self._http_post("/api/v2/public/redeem", {"code": cdk}, timeout=10)
            used = data.get("number_changes_used", 0)
            limit = data.get("number_changes_limit", 20)
            phone = data.get("phone_number", "")
            if phone and not phone.startswith("+"):
                phone = "+" + phone
            msg = (
                f"CDK有效！卡密: {cdk} | 已配号码: {phone} ({data.get('region_label', '')}) | "
                f"剩余换号: {limit - used}次 | 到期: {data.get('expiry_label', '')} | "
                f"号池可用: {pool_stats.get('available', 0)}张 (总数: {pool_stats.get('total', 0)}张)"
            )
            return {
                "message": msg,
                "phone_number": phone,
                "region_label": data.get("region_label", ""),
                "project_name": data.get("project_name", ""),
                "expiry_label": data.get("expiry_label", ""),
                "remaining_changes": limit - used,
                "pool_stats": pool_stats,
            }
        except Exception as e:
            return {
                "message": f"CDK {cdk} 状态异常: {e} | 号池可用: {pool_stats.get('available', 0)}张",
                "phone_number": "",
                "region_label": "",
                "project_name": "",
                "expiry_label": "",
                "remaining_changes": 0,
                "pool_stats": pool_stats,
            }


# ---------------------------------------------------------------------------
# 工厂 + 回调控制器（注入到 auth_flow）
# ---------------------------------------------------------------------------


def create_sms_provider(provider_key: str, config: dict) -> BaseSmsProvider:
    """从配置创建 provider 实例。

    provider_key: smsbower / herosms
    config 字段：sms_api_key / sms_country / sms_service / sms_max_price / sms_price /
                sms_reuse_phone / sms_phone_success_max
    """
    pk = (provider_key or "").lower().strip()
    api_key = str(config.get("sms_api_key") or "").strip()
    if not api_key and pk not in ("cdk_sms", "cdk", "ndk", "ndk_cdk", "lubansms"):
        raise RuntimeError(f"{pk} 未配置 API Key")
    country = str(config.get("sms_country") or "").strip()
    service = str(config.get("sms_service") or "").strip() or "dr"
    # 接码平台 API 默认直连。OAuth/注册用的出口代理（日本住宅等）打不通 smsbower.page。
    # 只有显式配置 sms_proxy 才走代理。
    proxy = (str(config.get("sms_proxy") or "")).strip() or None

    price_spec = config.get("sms_price") or config.get("sms_max_price") or config.get("sms_price_spec")
    min_p, max_p, exact_p = parse_price_spec(price_spec)
    if "sms_min_price" in config:
        min_p = _safe_float(config.get("sms_min_price"), min_p)
    if "sms_exact_price" in config:
        exact_p = _safe_float(config.get("sms_exact_price"), exact_p)

    reuse = _safe_bool(config.get("sms_reuse_phone"), False)
    succ_max = max(0, _safe_int(config.get("sms_phone_success_max"), 3))

    provider_ids = str(config.get("sms_provider_ids") or config.get("providerIds") or config.get("sms_operator") or config.get("operator") or "").strip()
    except_provider_ids = str(config.get("sms_except_provider_ids") or config.get("exceptProviderIds") or "").strip()
    phone_exception = str(config.get("sms_phone_exception") or config.get("phoneException") or "").strip()

    if pk in ("smsbower", "sms_bower"):
        return SmsBowerProvider(api_key=api_key,
                                default_service=service,
                                default_country=country or SMS_DEFAULT_COUNTRY,
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
                                phone_success_max=succ_max)
    if pk in ("herosms", "hero_sms"):
        return SmsBowerProvider(api_key=api_key,
                                base_url="https://hero-sms.com/stubs/handler_api.php",
                                default_service=service,
                                default_country=country or SMS_DEFAULT_COUNTRY,
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
                                phone_success_max=succ_max)
    if pk in ("cdk_sms", "cdk", "ndk", "ndk_cdk", "lubansms"):
        base_url = str(config.get("sms_cdk_url") or "https://ndk.cc.cd").strip()
        return CdkSmsProvider(
            api_key=api_key,
            base_url=base_url,
            proxy=proxy,
        )
    raise RuntimeError(f"未知接码服务: {provider_key}")


class PhoneCallbackController:
    """把 SMS provider 包装成两阶段回调，注入到 auth_flow.add_phone 流程。

    用法（在 auth_flow._handle_add_phone_verification 里）：
        controller = PhoneCallbackController(...)
        phone = controller.get_phone()         # 阶段1：租号
        flow._add_phone_send(phone)
        ...
        code = controller.get_code()           # 阶段2：等 SMS 验证码
        flow._phone_otp_validate(code)
        controller.report_success()            # 成功
        # 失败时 controller.cancel() / mark_code_failed()
    """

    def __init__(
        self,
        provider_key: str,
        config: dict,
        *,
        service: str = "openai",
        country: str = "",
        log_fn: Optional[Callable[[str], None]] = None,
        auto_select_country: bool = False,
    ):
        self.provider_key = provider_key
        self.config = dict(config or {})
        self.service = service
        self.country = country
        self.log = log_fn or logger.info
        self.auto_select_country = bool(auto_select_country)
        self.provider: Optional[BaseSmsProvider] = None
        self.activation: Optional[SmsActivation] = None
        self.completed = False
        self._verify_lock_acquired = False

    def _provider(self) -> BaseSmsProvider:
        if self.provider is None:
            self.provider = create_sms_provider(self.provider_key, self.config)
        return self.provider

    def get_phone(self) -> str:
        """阶段 1：租手机号（已带 +）。"""
        provider = self._provider()
        is_cdk = isinstance(provider, CdkSmsProvider) or self.provider_key in ("cdk_sms", "cdk", "ndk", "ndk_cdk", "lubansms")

        # ── CDK 卡密兑换模式专有处理与清晰日志 ──
        if is_cdk:
            if hasattr(provider, "log_fn"):
                provider.log_fn = self.log
            self.log("🎟️ 正在准备通过 CDK 卡密兑换中心分配手机号码 (平台: ndk.cc.cd)...")
            try:
                self.activation = provider.get_number(
                    service="openai",
                    country="44",
                    country_candidates=["44"],
                )
            except Exception as exc:
                self._release_lock()
                raise

            meta = self.activation.metadata or {}
            cdk = meta.get("cdk") or self.activation.activation_id
            phone = self.activation.phone_number
            region = meta.get("region_label") or self.activation.country or "英国 · OpenAI / ChatGPT"
            rem_changes = meta.get("number_changes_limit", 20) - meta.get("number_changes_used", 0)
            expiry = meta.get("expiry_label") or ""
            expiry_tip = f" · 到期: {expiry}" if expiry else ""
            self.log(f"✅ CDK 卡密 [{cdk}] 兑换成功！已分配手机号码: {phone} (项目: {meta.get('project_name', 'OpenAI/ChatGPT')}, 地区: {region}, 剩余免费换号: {rem_changes}次{expiry_tip})")
            return phone

        if getattr(provider, "reuse_phone_to_max", False) and isinstance(provider, SmsBowerProvider) and not self._verify_lock_acquired:
            _SMS_VERIFY_LOCK.acquire()
            self._verify_lock_acquired = True

        allowed_raw = str(self.config.get("sms_allowed_countries") or "").strip()
        allowed_list = [c.strip() for c in allowed_raw.replace(";", ",").split(",") if c.strip()]

        effective_country = self.country
        country_candidates: list[str] = []

        if not self.auto_select_country and effective_country and str(effective_country).upper() != "AUTO":
            # 用户指定了单一固定国家：严格锁定该国家，禁止轮询其它任何国家
            country_candidates = [effective_country]
        elif self.auto_select_country and allowed_list:
            country_candidates = list(allowed_list)
        elif self.auto_select_country and isinstance(provider, SmsBowerProvider):
            try:
                best = provider.get_best_country(
                    service=self.service,
                    min_stock=_safe_int(self.config.get("sms_auto_min_stock"), 20),
                    max_price=_safe_float(self.config.get("sms_auto_max_price"), 0),
                    strict_whitelist=_safe_bool(self.config.get("sms_strict_whitelist"), False),
                )
                if best:
                    country_candidates = [best]
            except Exception:
                pass
        elif effective_country and str(effective_country).upper() != "AUTO":
            country_candidates = [effective_country]
        elif allowed_list:
            country_candidates = list(allowed_list)
        else:
            country_candidates = [SMS_DEFAULT_COUNTRY]

        if not country_candidates:
            country_candidates = [SMS_DEFAULT_COUNTRY]

        country_label_log = ",".join(
            f"{c}({SMS_COUNTRY_NAMES_CN.get(c, '?')})" for c in country_candidates[:5]
        )
        self.log(f"📱 准备租号: provider={self.provider_key} service={self.service} 候选={country_label_log}{' ...' if len(country_candidates) > 5 else ''}")
        try:
            self.activation = provider.get_number(
                service=self.service,
                country=country_candidates[0],
                country_candidates=country_candidates,
            )
        except Exception as exc:
            self._release_lock()
            raise

        meta = self.activation.metadata or {}
        reused = bool(meta.get("reused"))
        used_country = self.activation.country or country_candidates[0]
        used_country_label = f"{used_country} {SMS_COUNTRY_NAMES_CN.get(used_country, '')}"
        cost = meta.get("cost")
        op_id = meta.get("operator") or ""
        cost_tip = f" 金额={cost}" if cost is not None else ""
        op_tip = f" 线路={op_id}" if op_id else ""
        self.log(f"✅ 已租到号码{'(复用)' if reused else ''}: {self.activation.phone_number} "
                 f"国家={used_country_label}{cost_tip}{op_tip} (activation_id={self.activation.activation_id})")
        return self.activation.phone_number

    def get_code(self, timeout: int = 180) -> str:
        """阶段 2：等待 SMS 验证码。"""
        if not self.activation:
            raise RuntimeError("PhoneCallbackController: 未先 get_phone")
        provider = self._provider()
        is_cdk = isinstance(provider, CdkSmsProvider) or self.provider_key in ("cdk_sms", "cdk", "ndk", "ndk_cdk", "lubansms")
        cdk = (self.activation.metadata or {}).get("cdk") or self.activation.activation_id

        if not is_cdk:
            self.log(f"⏳ 等待 SMS 验证码... (activation_id={self.activation.activation_id} timeout={timeout}s)")

        code = provider.get_code(self.activation.activation_id, timeout=timeout)
        if code:
            if not is_cdk:
                self.log(f"✅ 收到 SMS 验证码: {code}")
            if getattr(provider, "auto_report_success_on_code", True):
                self.report_success()
        else:
            if is_cdk:
                self.log(f"⚠️ 未收到短信验证码 (CDK: [{cdk}], 手机号: {self.activation.phone_number})")
            else:
                self.log(f"⚠️ 未收到 SMS 验证码: activation_id={self.activation.activation_id}")
        return code

    def report_success(self) -> None:
        if self.activation and self.provider and not self.completed:
            try:
                self.provider.report_success(self.activation.activation_id)
            except Exception as e:
                logger.warning("report_success 失败: %s", e)
            self.completed = True
            is_cdk = isinstance(self.provider, CdkSmsProvider) or self.provider_key in ("cdk_sms", "cdk", "ndk", "ndk_cdk", "lubansms")
            cdk = (self.activation.metadata or {}).get("cdk") or self.activation.activation_id
            if is_cdk:
                self.log(f"🎉 CDK [{cdk}] 本轮接码已成功完成并已安全记账！")
            else:
                self.log(f"🎉 已标记号码成功完成: activation_id={self.activation.activation_id}")
        self._release_lock()

    def mark_code_failed(self, reason: str = "") -> None:
        if self.activation and self.provider:
            try:
                self.provider.mark_code_failed(self.activation.activation_id, reason=reason)
            except Exception:
                pass

    def mark_send_succeeded(self) -> None:
        if self.activation and self.provider:
            try:
                self.provider.mark_send_succeeded(self.activation.activation_id)
            except Exception:
                pass

    def mark_send_failed(self, reason: str = "") -> None:
        if self.activation and self.provider:
            is_cdk = isinstance(self.provider, CdkSmsProvider) or self.provider_key in ("cdk_sms", "cdk", "ndk", "ndk_cdk", "lubansms")
            cdk = (self.activation.metadata or {}).get("cdk") or self.activation.activation_id
            if is_cdk:
                self.log(f"🔄 手机号已被 OpenAI 拒绝 ({reason})，正在为 CDK [{cdk}] 申请更换新号码...")
            try:
                self.provider.mark_send_failed(self.activation.activation_id, reason=reason)
            except Exception:
                pass

    def set_resend_callback(self, callback: Optional[Callable[[], None]]) -> None:
        try:
            self._provider().set_resend_callback(callback)
        except Exception:
            pass

    def cleanup(self) -> None:
        """流程结束（成功或失败）调用：释放未完成的号、解锁。"""
        if self.activation and not self.completed and self.provider:
            try:
                self.provider.cancel(self.activation.activation_id)
                self.log(f"🗑️ 已释放未使用号码: activation_id={self.activation.activation_id}")
            except Exception:
                pass
        self._release_lock()

    def _release_lock(self) -> None:
        if self._verify_lock_acquired:
            try:
                _SMS_VERIFY_LOCK.release()
            except RuntimeError:
                pass
            self._verify_lock_acquired = False


# ---------------------------------------------------------------------------
# 简单 CLI 测试
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        print("用法: python sms_provider.py <provider_key> <api_key> [country]")
        sys.exit(1)
    pk = sys.argv[1]
    key = sys.argv[2]
    cc = sys.argv[3] if len(sys.argv) > 3 else ""
    p = create_sms_provider(pk, {"sms_api_key": key, "sms_country": cc})
    print(f"余额: {p.get_balance()}")
