"""HeroSMS：与 SmsBower 同协议，仅 API 入口不同。"""
from __future__ import annotations

from .smsbower import SmsBowerProvider
from .base import register


@register
class HeroSmsProvider(SmsBowerProvider):
    kind = "herosms"
    aliases = ("hero_sms",)
    display_name = "HeroSMS"
    short_label = "20分退"
    description = "与 SmsBower 同协议，号码约 20 分钟未用自动退款"
    sort_order = 20
    DEFAULT_BASE_URL = "https://hero-sms.com/stubs/handler_api.php"
    timeout_hint = "推荐 60~85 秒。超过 90 秒容易导致 OpenAI 授权会话过期。"
