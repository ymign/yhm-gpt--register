"""CDK / 鲁班接码：卡密兑换号池。"""
from __future__ import annotations

import logging
import re
import threading
from typing import Callable, Optional

import requests

from .base import BaseSmsProvider, SmsActivation, register

logger = logging.getLogger(__name__)

@register
class CdkSmsProvider(BaseSmsProvider):
    """ndk.cc.cd / 鲁班接码 (LubanSMS) 卡密兑换接码 Provider。

    支持单卡密、多卡密及全自动【CDK号池】调度：
    - 无需固定 API Key，自动从 SQLite 号池 (sms_cdk_pool) 申领可用卡密
    - 针对支持多次接码的 CDK，成功接码后自动累计次数并持久保持可用状态，绝不提前废弃
    - 当平台返回 409(到期/取消) 或 422(无效) 时自动作废坏卡并轮换下一个可用卡密
    - 号池耗尽时精准报错阻断，提醒主人导入新卡密
    """

    auto_report_success_on_code = True

    kind = "cdk_sms"
    aliases = ("cdk", "ndk", "ndk_cdk", "lubansms")
    display_name = "CDK 卡密兑换"
    short_label = "ndk.cc.cd"
    description = "从 CDK 号池提取卡密兑换号码，被拒自动免费换号，多次卡支持长期复用"
    sort_order = 30
    needs_api_key = False
    uses_cdk_pool = True
    uses_country = False
    uses_price_tiers = False
    uses_provider_ids = False
    uses_reuse_phone = False
    uses_auto_country = False
    default_country = "44"
    default_service = "openai"
    default_timeout = 35
    recommended_timeout = 35
    max_timeout = 60
    timeout_hint = "CDK 推荐 30~35 秒。超过 45 秒容易导致 OpenAI 授权会话过期。"

    @classmethod
    def from_config(cls, config: dict) -> "CdkSmsProvider":
        api_key = str(config.get("sms_api_key") or "").strip()
        base_url = str(config.get("sms_cdk_url") or "https://ndk.cc.cd").strip()
        proxy = (str(config.get("sms_proxy") or "")).strip() or None
        return cls(api_key=api_key, base_url=base_url, proxy=proxy)

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

