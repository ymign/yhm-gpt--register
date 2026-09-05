"""接码渠道包 —— 加新渠道只改这个目录。

用法（registrar / app / oauth_export 的唯一入口）：

    from sms_providers import create_sms_provider, list_providers

    provider = create_sms_provider("smsbower", settings)

加一种新接码渠道：

    1. 新建 sms_providers/xxx.py，继承 BaseSmsProvider，实现
       get_number() / get_code() / cancel() / from_config()
       并声明 kind、display_name、能力开关（uses_country / uses_cdk_pool 等）
    2. 在本文件底部的「注册区」加一行 import

核心库（auth_flow / registrar / db / app / oauth_export）一行不动。
WebUI 接码配置页和 OAuth 弹窗按 list_providers() 动态渲染。
"""
from __future__ import annotations

from .base import (  # noqa: F401
    BaseSmsProvider,
    ConfigField,
    SmsActivation,
    canonicalize_kind,
    create_sms_provider,
    get_provider_class,
    known_kinds,
    list_providers,
    provider_display_name,
    register,
    uses_cdk_pool,
)
from .controller import PhoneCallbackController  # noqa: F401
from .util import (  # noqa: F401
    OPENAI_SMS_COUNTRIES,
    SMS_COUNTRY_NAMES_CN,
    SMS_DEFAULT_COUNTRY,
    SMS_DEFAULT_SERVICE,
    country_label,
    parse_price_spec,
)

# ════════════════════════════════════════════════════════════
#  注册区 —— 加渠道在这里加一行 import 即可
#  （import 时会触发模块内的 @register 装饰器完成注册）
# ════════════════════════════════════════════════════════════

from . import smsbower  # noqa: F401,E402  kind="smsbower"
from . import herosms   # noqa: F401,E402  kind="herosms"
from . import cdk_sms   # noqa: F401,E402  kind="cdk_sms"

from .smsbower import SmsBowerProvider  # noqa: F401,E402
from .herosms import HeroSmsProvider  # noqa: F401,E402
from .cdk_sms import CdkSmsProvider  # noqa: F401,E402

__all__ = [
    "BaseSmsProvider",
    "SmsActivation",
    "ConfigField",
    "SmsBowerProvider",
    "HeroSmsProvider",
    "CdkSmsProvider",
    "PhoneCallbackController",
    "register",
    "get_provider_class",
    "create_sms_provider",
    "list_providers",
    "canonicalize_kind",
    "known_kinds",
    "uses_cdk_pool",
    "provider_display_name",
    "parse_price_spec",
    "SMS_COUNTRY_NAMES_CN",
    "OPENAI_SMS_COUNTRIES",
    "SMS_DEFAULT_COUNTRY",
    "SMS_DEFAULT_SERVICE",
    "country_label",
]
