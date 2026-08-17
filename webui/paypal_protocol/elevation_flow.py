"""Pure-protocol guest-to-member identity-elevation mode.

The original PayPalFlow remains untouched and selectable.  This variant adds a
strict EC-token gate plus a post-signup identity hydration pass before the
existing authorize phase.  It does not launch Chromium or require a manual
review page.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
import urllib.parse

from loguru import logger

from .flow import PayPalFlow
from .graphql import (
    BUYER_CONTEXT_QUERY,
    BUYER_FUNDING_CONTEXT_QUERY,
    CHECKOUT_SESSION_DATA_QUERY,
)
from .session import sanitize_for_log


class IdentityElevationPayPalFlow(PayPalFlow):
    """PayPal protocol flow with an explicit guest-to-member hydration step."""

    _strict_buyer_context = True

    def _load_review_context(self, url: str, referer: str, max_redirects: int = 8):
        # Once AE BuyerContext has been hydrated, reloading Signup/Hermes can
        # downgrade the same checkout back to ANONYMOUS. Keep the established
        # member session and let the final BuyerContext/authorize calls proceed
        # against the existing EC cookies instead of navigating again.
        if (
            str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}
            and getattr(self, "_ae_pre_hydrated", False)
        ):
            logger.info(
                "AE/BA pre-hydrated member session preserved; skipping destructive review navigation"
            )
            return SimpleNamespace(
                status_code=204,
                content=b"",
                text="",
                headers={},
                url=url,
            )

        # AE checkout pages may clear the newly-issued EUAT cookie before the
        # member BuyerContext query runs. Preserve the signup token across the
        # HTML redirect chain; a later GraphQL buyer response may still refresh it.
        saved_euat = str(getattr(self.state, "euat_token", "") or "")
        response = super()._load_review_context(url, referer, max_redirects)
        if (
            str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}
            and saved_euat
            and not self.state.euat_token
        ):
            logger.info("AE/BA review navigation cleared EUAT; restoring signup access token")
            self.session.set_euat_token(saved_euat)
        return response

    @staticmethod
    def _require_checkout_session(result) -> dict:
        item = result[0] if isinstance(result, list) and result else result
        if not isinstance(item, dict):
            raise RuntimeError("IDENTITY_ELEVATION_CONTEXT_INVALID: checkout response type")
        if item.get("errors"):
            detail = ", ".join(
                str(error.get("message") or error.get("name") or "unknown error")
                for error in item.get("errors") or []
                if isinstance(error, dict)
            )
            raise RuntimeError(
                "IDENTITY_ELEVATION_CONTEXT_REJECTED: " + (detail or "GraphQL error")
            )
        checkout = (item.get("data") or {}).get("checkoutSession")
        if not isinstance(checkout, dict):
            raise RuntimeError("IDENTITY_ELEVATION_CONTEXT_MISSING: checkoutSession")
        if checkout.get("checkoutSessionType") != "BILLING_WITHOUT_PURCHASE":
            raise RuntimeError("IDENTITY_ELEVATION_CONTEXT_TYPE_MISMATCH")
        return checkout

    def _phase2_create_account(self):
        result = super()._phase2_create_account()
        if not self.state.ec_token or not self.state.ec_token.startswith("EC-"):
            raise RuntimeError(
                "IDENTITY_ELEVATION_EC_MISSING: onboarding did not produce an EC checkout token"
            )
        checkout_result = self.session.graphql(
            "CheckoutSessionDataQuery",
            CHECKOUT_SESSION_DATA_QUERY,
            {"token": self.state.ec_token},
        )
        self._require_checkout_session(checkout_result)
        if not self.state.signup_url or not self.state.content_identifier:
            raise RuntimeError("IDENTITY_ELEVATION_SIGNUP_CONTEXT_MISSING")
        self.state.signup_context_ready = True
        logger.info("Identity-elevation EC/signup context validated")
        return result

    def _phase3_signup_and_2fa(self):
        if not self.state.ec_token or not self.state.ec_token.startswith("EC-"):
            raise RuntimeError("IDENTITY_ELEVATION_EC_MISSING: signup requires an EC checkout token")
        if not self.state.signup_context_ready:
            raise RuntimeError("IDENTITY_ELEVATION_SIGNUP_CONTEXT_NOT_READY")
        return super()._phase3_signup_and_2fa()

    @staticmethod
    def _iter_funding_instruments(checkout: dict) -> list[dict]:
        """Collect funding instruments from fundingOptions.allPlans (correct path)."""
        options = checkout.get("fundingOptions")
        options = options if isinstance(options, dict) else {}
        instruments: list[dict] = []
        seen: set[str] = set()
        for plan in options.get("allPlans") or []:
            if not isinstance(plan, dict):
                continue
            for source in plan.get("fundingSources") or []:
                if not isinstance(source, dict):
                    continue
                instrument = source.get("fundingInstrument")
                instrument = instrument if isinstance(instrument, dict) else {}
                instrument_id = str(instrument.get("id") or "")
                if not instrument_id or instrument_id in seen:
                    continue
                seen.add(instrument_id)
                instruments.append({
                    "id": instrument_id,
                    "type": str(instrument.get("type") or "").upper(),
                })
        return instruments

    @staticmethod
    def _pick_preferred_funding_id(instruments: list[dict], selected_id: str = "") -> str:
        if selected_id:
            return selected_id
        if not instruments:
            return ""
        # Prefer real cards/banks, then PayPal balance, then anything else.
        for preferred_type in ("CREDIT", "DEBIT", "CARD", "BANK", "BANK_ACCOUNT"):
            for item in instruments:
                if item.get("type") == preferred_type:
                    return str(item["id"])
        for item in instruments:
            if item.get("type") == "BALANCE":
                return str(item["id"])
        return str(instruments[0]["id"])

    @staticmethod
    def _funding_summary(checkout: dict) -> dict:
        options = checkout.get("fundingOptions")
        options = options if isinstance(options, dict) else {}
        selected = options.get("fundingInstrument")
        selected = selected if isinstance(selected, dict) else {}
        instruments = IdentityElevationPayPalFlow._iter_funding_instruments(checkout)
        available_ids = {item["id"] for item in instruments}
        return {
            "selected": bool(selected.get("id")),
            "selected_id": str(selected.get("id") or ""),
            "available": bool(available_ids),
            "available_count": len(available_ids),
            "instruments": instruments,
        }

    @staticmethod
    def _resolve_need_credit_contingency(
        funding_error_messages: list[str],
        funding_checkpoints: list[str],
        funding: dict,
    ) -> dict:
        """Handle recoverable funding contingencies for BILLING_WITHOUT_PURCHASE.

        Fresh guest→member accounts often surface NEED_CREDIT_CARD* or even
        PAYER_ACCOUNT_RESTRICTED on fundingOptions after addCard is rejected.
        That restriction is about charging, not about approving a $0/€0 BA.
        If any instrument exists, pin it.  Otherwise soft-waive and authorize
        with balance OPT_IN instead of aborting before the authorize mutation.
        """
        soft_errors = {
            "NEED_CREDIT_CARD",
            "NEED_CREDIT_CARD_OR_BANK_ACCOUNT",
            "NEED_BANK_ACCOUNT",
            "PAYER_ACCOUNT_RESTRICTED",
        }
        matched = [msg for msg in funding_error_messages if msg in soft_errors]
        if not matched:
            return {
                "funding_errors": funding_error_messages,
                "funding_checkpoints": funding_checkpoints,
                "funding_preferred_id": funding.get("selected_id") or "",
                "funding_soft_waived": False,
                "funding_waived_reason": "",
            }

        preferred_id = IdentityElevationPayPalFlow._pick_preferred_funding_id(
            funding.get("instruments") or [],
            selected_id=str(funding.get("selected_id") or ""),
        )
        remaining_errors = [msg for msg in funding_error_messages if msg not in soft_errors]
        remaining_checkpoints = [
            checkpoint
            for checkpoint in funding_checkpoints
            if checkpoint != "getCheckoutSession"
        ] if not remaining_errors else funding_checkpoints
        waived_reason = matched[0]

        if preferred_id:
            logger.info(
                "{} recovered by selecting funding instrument {}",
                waived_reason,
                preferred_id,
            )
            return {
                "funding_errors": remaining_errors,
                "funding_checkpoints": remaining_checkpoints,
                "funding_preferred_id": preferred_id,
                "funding_soft_waived": False,
                "funding_waived_reason": "",
            }

        logger.warning(
            "{} with no funding instruments; "
            "soft-waiving for BILLING_WITHOUT_PURCHASE / zero-amount trial and "
            "continuing authorize with balance OPT_IN",
            waived_reason,
        )
        return {
            "funding_errors": remaining_errors,
            "funding_checkpoints": remaining_checkpoints,
            "funding_preferred_id": "",
            "funding_soft_waived": True,
            "funding_waived_reason": waived_reason,
        }

    def _query_elevated_context(self, token: str, referer: str) -> dict:
        headers = {
            "Referer": referer,
            "X-App-Name": "checkoutuinodeweb",
            "X-PayPal-Internal-EUAT": self.state.euat_token,
            "PayPal-Client-Context": token,
            "PayPal-Client-Metadata-Id": self.state.paypal_client_metadata_id,
            "X-Country": self.address.country,
            "X-Locale": self.locale,
        }
        result = self.session.graphql(
            "BuyerFundingContextQuery",
            BUYER_FUNDING_CONTEXT_QUERY,
            {"token": token},
            extra_headers=headers,
            endpoint="https://www.paypal.com/graphql/",
        )
        funding_obj = result[0] if isinstance(result, list) and result else result
        funding_obj = funding_obj if isinstance(funding_obj, dict) else {}
        funding_errors = [
            item for item in (funding_obj.get("errors") or [])
            if isinstance(item, dict)
        ]
        funding_error_messages = [
            str(item.get("message") or item.get("name") or "").strip()
            for item in funding_errors
            if str(item.get("message") or item.get("name") or "").strip()
        ]
        funding_checkpoints = sorted({
            str(checkpoint)
            for item in funding_errors
            for checkpoint in (item.get("checkpoints") or [])
            if str(checkpoint)
        })

        fatal_messages = {
            "PAYER_INVALID_FOR_PAYMENT",
            "TRANSACTION_REFUSED",
            "ACCOUNT_RESTRICTED",
            "ACCOUNT_LOCKED",
        }

        def checkout_from(payload: dict) -> dict:
            data = payload.get("data")
            data = data if isinstance(data, dict) else {}
            checkout = data.get("checkoutSession")
            return checkout if isinstance(checkout, dict) else {}

        checkout = checkout_from(funding_obj)
        buyer = checkout.get("buyer")
        buyer = buyer if isinstance(buyer, dict) else {}

        # Funding queries commonly return partial buyer data together with a
        # recoverable NEED_CREDIT_CARD contingency. Preserve that buyer instead
        # of discarding the whole response. Fall back only when buyer identity
        # is genuinely absent.
        if not str(buyer.get("userId") or ""):
            logger.warning(
                "Identity-elevation funding query requires buyer-only fallback: {}",
                json.dumps(sanitize_for_log(funding_obj), ensure_ascii=False)[:1000],
            )
            fallback = self.session.graphql(
                "BuyerContextQuery",
                BUYER_CONTEXT_QUERY,
                {"token": token},
                extra_headers=headers,
                endpoint="https://www.paypal.com/graphql/",
            )
            fallback_obj = fallback[0] if isinstance(fallback, list) and fallback else fallback
            fallback_obj = fallback_obj if isinstance(fallback_obj, dict) else {}
            checkout = checkout_from(fallback_obj)
            buyer = checkout.get("buyer")
            buyer = buyer if isinstance(buyer, dict) else {}

        auth = buyer.get("auth")
        auth = auth if isinstance(auth, dict) else {}
        refreshed_euat = str(auth.get("accessToken") or "")
        if refreshed_euat:
            self.session.set_euat_token(refreshed_euat)
        user_id = str(buyer.get("userId") or "")
        if user_id:
            self.state.user_id = user_id
        funding = self._funding_summary(checkout)
        resolved = self._resolve_need_credit_contingency(
            funding_error_messages,
            funding_checkpoints,
            funding,
        )
        funding_error_messages = resolved["funding_errors"]
        funding_checkpoints = resolved["funding_checkpoints"]
        preferred_id = resolved["funding_preferred_id"]
        fatal_contingency = next(
            (message for message in funding_error_messages if message in fatal_messages),
            "",
        )
        selected_id = funding["selected_id"] or preferred_id
        return {
            "buyer_ready": bool(user_id and self.state.euat_token),
            "user_id": user_id,
            "auth_refreshed": bool(refreshed_euat),
            "funding_selected": bool(selected_id),
            "funding_selected_id": selected_id,
            "funding_preferred_id": preferred_id,
            "funding_available": funding["available"] or bool(preferred_id) or resolved["funding_soft_waived"],
            "funding_available_count": funding["available_count"],
            "funding_errors": funding_error_messages,
            "funding_checkpoints": funding_checkpoints,
            "funding_soft_waived": resolved["funding_soft_waived"],
            "funding_waived_reason": resolved["funding_waived_reason"],
            "fatal_contingency": fatal_contingency,
        }

    def _protocol_identity_elevation(self) -> dict:
        token = self.state.ec_token
        if not token or not token.startswith("EC-"):
            raise RuntimeError("IDENTITY_ELEVATION_EC_MISSING")
        if not self.state.euat_token:
            raise RuntimeError("IDENTITY_ELEVATION_EUAT_MISSING")
        if not self.state.signup_context_ready:
            raise RuntimeError("IDENTITY_ELEVATION_SIGNUP_CONTEXT_NOT_READY")

        logger.info("--- [阶段4: 身份提升] 正在将 Guest 结账提升为 Member 会员认证结账 ---")
        self.session.set_euat_token(self.state.euat_token)

        # Re-enter checkoutweb as an authenticated member on the exact EC token.
        signup_url = self.state.signup_url or (
            "https://www.paypal.com/checkoutweb/signup?" + urllib.parse.urlencode({
                "ssrt": self.state.ssrt,
                "ul": "1",
                "modxo_redirect_reason": "guest_user",
                "locale.x": self.locale,
                "country.x": self.address.country,
                "ba_token": self.ba_token,
                "token": token,
                "rcache": "1",
                "cookieBannerVariant": "hidden",
            })
        )
        saved_signup_euat = str(self.state.euat_token or "")

        # AE can invalidate the freshly-issued signup token as soon as the
        # signup/Hermes HTML navigation begins. Hydrate the EC buyer first,
        # while the access token is still fresh, then enter the review route.
        if str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}:
            logger.info("AE/BA pre-navigation member hydration started")
            immediate_context = self._query_elevated_context(token, signup_url)
            logger.info(
                "AE/BA pre-navigation hydration: buyer={} errors={} fatal={}",
                immediate_context["buyer_ready"],
                immediate_context["funding_errors"],
                immediate_context["fatal_contingency"] or "none",
            )
            if immediate_context["fatal_contingency"]:
                raise RuntimeError(
                    "IDENTITY_ELEVATION_PAYER_RESTRICTED: "
                    + immediate_context["fatal_contingency"]
                )
            if immediate_context["buyer_ready"]:
                self._ae_pre_hydrated = True
                self._last_elevation_context = immediate_context
                return immediate_context
            # A rejected probe may clear the EUAT cookie; restore it before the
            # existing review-based hydration attempt.
            if saved_signup_euat:
                self.session.set_euat_token(saved_signup_euat)

        self.session.get(
            signup_url,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": signup_url,
                "Upgrade-Insecure-Requests": "1",
            },
        )
        if (
            str(getattr(self.address, "country", "") or "").upper() in {"AE", "BA"}
            and saved_signup_euat
            and not self.state.euat_token
        ):
            logger.info("AE/BA signup navigation cleared EUAT; restoring signup access token")
            self.session.set_euat_token(saved_signup_euat)

        review_referer = (
            "https://www.paypal.com/webapps/hermes?"
            + urllib.parse.urlencode({
                "ssrt": self.state.ssrt,
                "ul": "1",
                "modxo_redirect_reason": "guest_user",
                "locale.x": self.locale,
                "country.x": self.address.country,
                "ba_token": self.ba_token,
                "token": token,
                "rcache": "1",
                "cookieBannerVariant": "hidden",
                "fromSignupLite": "true",
                "billingLite": "1",
            })
        )
        self._load_review_context(review_referer, signup_url)
        context = self._query_elevated_context(token, review_referer)
        logger.info(
            "Identity elevation context: buyer={} funding_selected={} funding_available={} "
            "count={} errors={} soft_waived={} fatal={}",
            context["buyer_ready"],
            context["funding_selected"],
            context["funding_available"],
            context["funding_available_count"],
            context["funding_errors"],
            context.get("funding_soft_waived"),
            context["fatal_contingency"] or "none",
        )
        if context["fatal_contingency"]:
            raise RuntimeError(
                "IDENTITY_ELEVATION_PAYER_RESTRICTED: " + context["fatal_contingency"]
            )
        if not context["buyer_ready"]:
            raise RuntimeError(
                "IDENTITY_ELEVATION_FAILED: member identity was not attached to the EC checkout"
            )
        self._last_elevation_context = context
        return context

    def _get_funding_preference(self) -> dict:
        """Return fundingPreference for this checkout.

        When the elevation context already selected / recovered a funding
        instrument, pin that instrument explicitly.  Otherwise default to
        PayPal balance (OPT_IN) instead of OPT_OUT: free/0元 trial agreements
        have no amount to charge, so forcing an external card is wrong and
        produces NEED_CREDIT_CARD_OR_BANK_ACCOUNT on fresh accounts that only
        have an empty balance.  Balance-first lets a $0/€0 agreement authorize
        cleanly and return to the merchant checkout.
        """
        context = getattr(self, "_last_elevation_context", None) or {}
        selected_id = (
            context.get("funding_selected_id")
            or context.get("funding_preferred_id")
            or ""
        )
        if selected_id:
            return {
                "selectedFundingInstrument": {
                    "id": selected_id,
                }
            }
        return {
            "balancePreference": "OPT_IN",
        }

    def _phase4_authorize(self) -> dict:
        try:
            context = self._protocol_identity_elevation()
        except RuntimeError as exc:
            reason = str(exc)
            country = str(getattr(self.address, "country", "") or "").upper()
            ec_token = str(getattr(self.state, "ec_token", "") or "")

            # AE sometimes creates a valid anonymous EC checkout but refuses to
            # attach the newly-created member identity. In that specific case,
            # preserve the current EC/session and try authorize as ANONYMOUS.
            # Hard payer restrictions remain terminal and are not downgraded.
            if (
                country != "AE"
                or "IDENTITY_ELEVATION_PAYER_RESTRICTED" in reason
                or not ec_token.startswith("EC-")
            ):
                raise

            logger.warning(
                "AE identity elevation unavailable; using current ANONYMOUS EC session once: {}",
                reason,
            )
            strict_before = getattr(self, "_strict_buyer_context", True)
            self._strict_buyer_context = False
            try:
                result = super()._phase4_authorize()
            finally:
                self._strict_buyer_context = strict_before

            if isinstance(result, dict):
                result.setdefault("buyer_mode", "ae_anonymous_fallback")
                result.setdefault(
                    "identity_elevation",
                    {
                        "buyer_ready": False,
                        "anonymous_fallback": True,
                        "reason": reason,
                    },
                )
            return result

        self.state.funding_preference = self._get_funding_preference()
        if context.get("funding_soft_waived"):
            logger.info(
                "Authorizing zero-amount BA with balance OPT_IN after soft-waiving {}",
                context.get("funding_waived_reason") or "NEED_CREDIT_CARD_OR_BANK_ACCOUNT",
            )
        elif context.get("funding_selected_id"):
            logger.info(
                "Authorizing with recovered funding instrument {}",
                context.get("funding_selected_id"),
            )
        result = super()._phase4_authorize()
        if isinstance(result, dict):
            result.setdefault("buyer_mode", "identity_elevation")
            result.setdefault("identity_elevation", context)
        return result
