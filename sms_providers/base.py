"""接码 provider 抽象层 + 注册表。

加一种新接码渠道：
    1. 新建 sms_providers/xxx.py，继承 BaseSmsProvider
    2. 声明 kind / display_name / 能力开关，实现 from_config()
    3. 在 sms_providers/__init__.py 注册区加一行 import

核心库（auth_flow / registrar / oauth_export / db / app）不用改。
WebUI 接码配置页和 OAuth 弹窗按 list_providers() 动态渲染。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Callable, Optional


class ConfigField:
    """一个配置项的元信息，供 WebUI 动态渲染表单。"""

    def __init__(
        self,
        key: str,
        label: str,
        *,
        type: str = "text",
        required: bool = False,
        placeholder: str = "",
        help: str = "",
    ):
        self.key = key
        self.label = label
        self.type = type
        self.required = required
        self.placeholder = placeholder
        self.help = help

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "placeholder": self.placeholder,
            "help": self.help,
        }


@dataclass
class SmsActivation:
    """一次手机号租用的句柄。"""

    activation_id: str
    phone_number: str  # E.164 格式，带 + 前缀
    country: str = ""
    metadata: dict = field(default_factory=dict)


class BaseSmsProvider(ABC):
    """所有接码渠道的基类。

    子类必须实现 get_number / get_code / cancel / from_config。
    能力开关供 WebUI 决定展示哪些配置块，加渠道时前端不用改。
    """

    # ── 身份 ──
    kind: str = ""
    aliases: tuple[str, ...] = ()
    display_name: str = ""
    short_label: str = ""
    description: str = ""
    sort_order: int = 100

    # ── 能力声明 ──
    needs_api_key: bool = True
    uses_cdk_pool: bool = False
    uses_country: bool = True
    uses_price_tiers: bool = False
    uses_provider_ids: bool = False
    uses_reuse_phone: bool = False
    uses_auto_country: bool = False

    # ── 默认运行参数 ──
    default_country: str = "52"
    default_service: str = "dr"
    default_timeout: int = 80
    recommended_timeout: int = 80
    max_timeout: int = 120
    timeout_hint: str = ""

    config_fields: list = []
    auto_report_success_on_code = True

    @classmethod
    def from_config(cls, config: dict) -> "BaseSmsProvider":
        raise NotImplementedError(f"{cls.__name__} 未实现 from_config")

    @classmethod
    def to_manifest(cls) -> dict:
        fields = []
        for f in cls.config_fields or []:
            fields.append(f.to_dict() if hasattr(f, "to_dict") else f)
        return {
            "kind": cls.kind,
            "aliases": list(cls.aliases or ()),
            "display_name": cls.display_name or cls.kind,
            "short_label": cls.short_label,
            "description": cls.description,
            "sort_order": int(cls.sort_order or 100),
            "needs_api_key": bool(cls.needs_api_key),
            "uses_cdk_pool": bool(cls.uses_cdk_pool),
            "uses_country": bool(cls.uses_country),
            "uses_price_tiers": bool(cls.uses_price_tiers),
            "uses_provider_ids": bool(cls.uses_provider_ids),
            "uses_reuse_phone": bool(cls.uses_reuse_phone),
            "uses_auto_country": bool(cls.uses_auto_country),
            "default_country": cls.default_country,
            "default_service": cls.default_service,
            "default_timeout": int(cls.default_timeout or 80),
            "recommended_timeout": int(cls.recommended_timeout or cls.default_timeout or 80),
            "max_timeout": int(cls.max_timeout or 120),
            "timeout_hint": cls.timeout_hint or "",
            "config_fields": fields,
        }

    @abstractmethod
    def get_number(
        self,
        *,
        service: str,
        country: str = "",
        country_candidates: Optional[list[str]] = None,
    ) -> SmsActivation:
        ...

    @abstractmethod
    def get_code(self, activation_id: str, *, timeout: int = 180) -> str:
        ...

    @abstractmethod
    def cancel(self, activation_id: str) -> bool:
        ...

    def get_balance(self) -> float:
        raise NotImplementedError

    def report_success(self, activation_id: str) -> bool:
        return True

    def mark_code_failed(self, activation_id: str, reason: str = "") -> None:
        return None

    def mark_send_failed(self, activation_id: str, reason: str = "") -> None:
        return None

    def mark_send_succeeded(self, activation_id: str) -> None:
        return None

    def set_resend_callback(self, callback: Optional[Callable[[], None]]) -> None:
        return None


# ════════════════════════════════════════════════════════════
#  注册表 + 工厂
# ════════════════════════════════════════════════════════════

_PROVIDERS: dict[str, type[BaseSmsProvider]] = {}
_ALIASES: dict[str, str] = {}


def register(provider_cls: type[BaseSmsProvider]) -> type[BaseSmsProvider]:
    """注册一个接码渠道。可直接当装饰器用。"""
    key = (getattr(provider_cls, "kind", "") or "").strip().lower()
    if not key:
        raise ValueError(f"{provider_cls.__name__} 必须定义唯一的 kind")
    if key in _PROVIDERS and _PROVIDERS[key] is not provider_cls:
        raise ValueError(f"kind='{key}' 已被 {_PROVIDERS[key].__name__} 占用")
    _PROVIDERS[key] = provider_cls
    for alias in getattr(provider_cls, "aliases", ()) or ():
        a = str(alias or "").strip().lower()
        if a:
            _ALIASES[a] = key
    return provider_cls


def canonicalize_kind(key: str) -> str:
    """把别名归一成已注册的 kind；未知返回空串。"""
    k = (key or "").strip().lower()
    if k in _PROVIDERS:
        return k
    return _ALIASES.get(k, "")


def get_provider_class(kind: str) -> type[BaseSmsProvider]:
    key = canonicalize_kind(kind)
    if not key:
        known = ", ".join(sorted(_PROVIDERS)) or "(空)"
        raise RuntimeError(f"未知接码服务: '{kind}'（已注册: {known}）")
    return _PROVIDERS[key]


def create_sms_provider(provider_key: str, config: dict) -> BaseSmsProvider:
    """业务侧唯一构造入口，替代原来的 if/else 路由。"""
    return get_provider_class(provider_key).from_config(config or {})


def list_providers() -> list[dict]:
    """给 WebUI：已注册渠道及其能力/配置项，按 sort_order 排列。"""
    items = [cls.to_manifest() for cls in _PROVIDERS.values()]
    items.sort(key=lambda x: (x.get("sort_order", 100), x.get("kind", "")))
    return items


def known_kinds() -> set[str]:
    return set(_PROVIDERS) | set(_ALIASES)


def uses_cdk_pool(kind: str) -> bool:
    try:
        return bool(get_provider_class(kind).uses_cdk_pool)
    except Exception:
        return False


def provider_display_name(kind: str) -> str:
    try:
        cls = get_provider_class(kind)
        return cls.display_name or cls.kind or str(kind)
    except Exception:
        return str(kind or "")
