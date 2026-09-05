"""兼容入口：历史代码 `from sms_provider import ...` 继续可用。

新代码请改为：

    from sms_providers import create_sms_provider, list_providers
"""
from sms_providers import *  # noqa: F401,F403
from sms_providers import (  # noqa: F401
    OPENAI_SMS_COUNTRIES,
    SMS_COUNTRY_NAMES_CN,
    SMS_DEFAULT_COUNTRY,
    SMS_DEFAULT_SERVICE,
    BaseSmsProvider,
    CdkSmsProvider,
    HeroSmsProvider,
    PhoneCallbackController,
    SmsActivation,
    SmsBowerProvider,
    canonicalize_kind,
    country_label,
    create_sms_provider,
    get_provider_class,
    list_providers,
    parse_price_spec,
    uses_cdk_pool,
)
