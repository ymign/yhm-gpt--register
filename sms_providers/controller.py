"""把 SMS provider 包装成两阶段回调，注入到 auth_flow.add_phone 流程。"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from .base import BaseSmsProvider, SmsActivation, create_sms_provider
from .util import (
    SMS_COUNTRY_NAMES_CN,
    SMS_DEFAULT_COUNTRY,
    _SMS_VERIFY_LOCK,
    _safe_bool,
    _safe_float,
    _safe_int,
)

logger = logging.getLogger(__name__)


class PhoneCallbackController:
    """两阶段回调：get_phone() → 业务侧发码 → get_code() → report_success()。"""

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
        is_cdk = bool(getattr(provider, "uses_cdk_pool", False))

        if is_cdk:
            if hasattr(provider, "log_fn"):
                provider.log_fn = self.log
            self.log(
                f"🎟️ 正在准备通过 CDK 卡密兑换中心分配手机号码 "
                f"(平台: {getattr(provider, 'base_url', '') or 'ndk.cc.cd'})..."
            )
            cdk_country = str(getattr(provider, "default_country", "") or "44")
            try:
                self.activation = provider.get_number(
                    service="openai",
                    country=cdk_country,
                    country_candidates=[cdk_country],
                )
            except Exception:
                self._release_lock()
                raise

            meta = self.activation.metadata or {}
            cdk = meta.get("cdk") or self.activation.activation_id
            phone = self.activation.phone_number
            region = meta.get("region_label") or self.activation.country or "英国 · OpenAI / ChatGPT"
            rem_changes = meta.get("number_changes_limit", 20) - meta.get("number_changes_used", 0)
            expiry = meta.get("expiry_label") or ""
            expiry_tip = f" · 到期: {expiry}" if expiry else ""
            self.log(
                f"✅ CDK 卡密 [{cdk}] 兑换成功！已分配手机号码: {phone} "
                f"(项目: {meta.get('project_name', 'OpenAI/ChatGPT')}, 地区: {region}, "
                f"剩余免费换号: {rem_changes}次{expiry_tip})"
            )
            return phone

        if (
            getattr(provider, "reuse_phone_to_max", False)
            and getattr(provider, "uses_reuse_phone", False)
            and not self._verify_lock_acquired
        ):
            _SMS_VERIFY_LOCK.acquire()
            self._verify_lock_acquired = True

        allowed_raw = str(self.config.get("sms_allowed_countries") or "").strip()
        allowed_list = [c.strip() for c in allowed_raw.replace(";", ",").split(",") if c.strip()]

        effective_country = self.country
        country_candidates: list[str] = []

        if not self.auto_select_country and effective_country and str(effective_country).upper() != "AUTO":
            country_candidates = [effective_country]
        elif self.auto_select_country and allowed_list:
            country_candidates = list(allowed_list)
        elif self.auto_select_country and hasattr(provider, "get_best_country"):
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
            country_candidates = [getattr(provider, "default_country", "") or SMS_DEFAULT_COUNTRY]

        if not country_candidates:
            country_candidates = [SMS_DEFAULT_COUNTRY]

        country_label_log = ",".join(
            f"{c}({SMS_COUNTRY_NAMES_CN.get(c, '?')})" for c in country_candidates[:5]
        )
        self.log(
            f"📱 准备租号: provider={self.provider_key} service={self.service} "
            f"候选={country_label_log}{' ...' if len(country_candidates) > 5 else ''}"
        )
        try:
            self.activation = provider.get_number(
                service=self.service,
                country=country_candidates[0],
                country_candidates=country_candidates,
            )
        except Exception:
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
        self.log(
            f"✅ 已租到号码{'(复用)' if reused else ''}: {self.activation.phone_number} "
            f"国家={used_country_label}{cost_tip}{op_tip} (activation_id={self.activation.activation_id})"
        )
        return self.activation.phone_number

    def get_code(self, timeout: int = 180) -> str:
        """阶段 2：等待 SMS 验证码。"""
        if not self.activation:
            raise RuntimeError("PhoneCallbackController: 未先 get_phone")
        provider = self._provider()
        is_cdk = bool(getattr(provider, "uses_cdk_pool", False))
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
            is_cdk = bool(getattr(self.provider, "uses_cdk_pool", False))
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
            is_cdk = bool(getattr(self.provider, "uses_cdk_pool", False))
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
