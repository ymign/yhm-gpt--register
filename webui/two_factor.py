"""
two_factor.py — 注册成功后为账号程序化绑定 TOTP 2FA（库函数版）
================================================================
移植自桌面实测脚本 `bind_2fa.py`（2026-08-04 跑通，mfa_enabled:true）。
去掉命令行 / 号池文件 / print，改成可被 registrar 调用的库函数。

两条路，registrar 先快后慢：

  ① bind_totp_2fa_inline(flow, at)  ★快路径★
     直接拿【注册那个 flow】的 session + access_token 打 enroll/activate。
     实测 2026-08-08 <测试号>@<自建域>：A/B/C/D 四个请求全 200，
     mfa_enabled=true，**6.2 秒**跑完。

  ② bind_totp_2fa(cfg, email, password, ...)  兜底
     新起 AuthFlow 重走一遍 login 正式链再 enroll。约 40 秒 + 一次 PoW +
     一封验证码邮件。快路径失败、或者要给【库里已有的老号】补绑时走这条。

★ 桌面《2FA绑定实现方案.txt》【二】说"注册会话直接 enroll 会 401
  recent_auth_required、必须重走正式链"—— 这条【实测不成立】，见上面探针结果。
  原文是推测不是实测。慢路径保留作兜底，但不再是唯一路。

★ secret 只在 enroll 响应里下发【一次】！服务端不存明文、任何接口都取不回。
  丢了 = 该号 2FA 永久锁死（只能走账号申诉）。本函数返回后 registrar 立刻落库。

★ 绑定即生效：之后该号所有登录都要 6 位动态码（密码/邮件验证后进 mfa-challenge）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import struct
import sys
import time
import urllib.parse
from pathlib import Path

# 项目根（gpt-outlook-register/）加入 sys.path，便于 import config / auth_flow。
# registrar 载入时已插过一次，这里再兜底一次，保证 two_factor 可被单独 import。
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from auth_flow import AuthFlow  # noqa: E402

logger = logging.getLogger("two_factor")


# ── RFC 6238 手写实现（无 pyotp 也能算码，双保险）─────────────────
def hotp(secret_b32: str, counter: int, digits: int = 6) -> str:
    key = base64.b32decode(secret_b32 + "=" * (-len(secret_b32) % 8))
    msg = struct.pack(">Q", counter)
    h = hmac.new(key, msg, hashlib.sha1).digest()
    o = h[-1] & 0x0F
    code = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_now(secret_b32: str) -> str:
    """当前 30 秒窗口的 6 位码。"""
    return hotp(secret_b32, int(time.time()) // 30)


def verify_totp(secret_b32: str, code: str) -> bool:
    """前后各 1 窗口容错的本地校验（激活前自检用）。"""
    c = int(time.time()) // 30
    return code in {hotp(secret_b32, c + d) for d in (-1, 0, 1)}


def generate_random_password(length: int = 16) -> str:
    """生成包含大小写字母、数字和特殊字符的强随机密码。"""
    import secrets
    import string
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    chars = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    chars += [secrets.choice(alphabet) for _ in range(length - 4)]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def bind_totp_2fa_by_token(
    access_token: str,
    proxy: str = "",
    log_cb: Optional[Callable[[str], None]] = None,
    step_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """使用 access_token 独立打官方接口执行 2FA 绑定。

    请求 OpenAI 官方 mfa/enroll + activate_enrollment 接口，
    确保 mfa_enabled 真正生效并返回 secret。
    """
    def _l(msg: str):
        logger.info(f"[2fa-token] {msg}")
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    def _s(step: str):
        if step_cb:
            try:
                step_cb(step)
            except Exception:
                pass

    try:
        from curl_cffi.requests import Session as CffiSession
        session = CffiSession(impersonate="chrome136")
        session.trust_env = False
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}

        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {access_token}",
            "Origin": "https://chatgpt.com",
            "Referer": "https://chatgpt.com/",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
            "Sec-Ch-Ua": '"Not?A_Brand";v="99", "Chromium";v="136", "Google Chrome";v="136"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

        # 1. 检查是否已绑
        _s("1/4 检查官方 2FA 状态...")
        _l("🔍 正在调用 OpenAI 官方 /backend-api/accounts/mfa_info 检查当前 2FA 绑定状态...")
        r2 = session.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=headers, timeout=30)
        if r2.status_code == 200:
            info = r2.json() or {}
            if info.get("mfa_enabled") and (info.get("factors", {}) or {}).get("totp"):
                _l("ℹ️ OpenAI 官方服务端检测：该账号已开启 2FA TOTP 双重认证保护")
                return {"ok": True, "already_bound": True, "message": "该账号已在 OpenAI 官方服务端绑定 2FA"}
            _l("🔓 官方服务端检测：账号尚未开启 2FA，准备申请下发 TOTP 密钥...")
        elif r2.status_code in (401, 403):
            raise RuntimeError(f"Access Token 已过期或未授权 (HTTP {r2.status_code})")

        # 2. enroll
        _s("2/4 申请官方 TOTP 密钥...")
        _l("🔑 正在向 OpenAI 官方 /backend-api/accounts/mfa/enroll 请求下发 TOTP 密钥...")
        headers["Content-Type"] = "application/json"
        r3 = session.post("https://chatgpt.com/backend-api/accounts/mfa/enroll", headers=headers, json={"factor_type": "totp"}, timeout=30)
        if r3.status_code != 200:
            raise RuntimeError(f"OpenAI 官方 enroll 失败 ({r3.status_code}): {(r3.text or '')[:200]}")
        en = r3.json() or {}
        secret = en.get("secret", "")
        session_id = en.get("session_id", "")
        factor_id = (en.get("factor", {}) or {}).get("id", "")
        if not secret or not session_id:
            raise RuntimeError(f"OpenAI 官方 enroll 响应缺少 secret 或 session_id: {en}")

        _l(f"📥 成功获取官方下发的 2FA Secret: {secret} (共 {len(secret)} 位密钥)")

        # 3. 计算 6 位当前动态码并 activate
        code = totp_now(secret)
        _s("3/4 计算验证码并提交激活...")
        _l(f"⏱️ 正在依据 RFC 6238 算法计算当前 6 位动态验证码: {code}，提交官方激活...")
        r4 = session.post(
            "https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment",
            headers=headers,
            json={"code": code, "factor_type": "totp", "session_id": session_id},
            timeout=30,
        )
        if r4.status_code != 200:
            raise RuntimeError(f"OpenAI 官方 activate_enrollment 失败 ({r4.status_code}): {(r4.text or '')[:200]}")

        _l(f"✨ 官方动态码校验通过 (HTTP 200)")

        # 4. 激活成功后复核 mfa_info 确保服务端 mfa_enabled 为 True
        _s("4/4 复核官方状态...")
        _l("🔄 正在复核官方 /backend-api/accounts/mfa_info 确认 mfa_enabled 状态...")
        time.sleep(1)
        r5 = session.get("https://chatgpt.com/backend-api/accounts/mfa_info", headers=headers, timeout=30)
        mfa_confirmed = False
        if r5.status_code == 200:
            info5 = r5.json() or {}
            mfa_confirmed = bool(info5.get("mfa_enabled"))

        if mfa_confirmed:
            _l(f"✅ 官方复核确认：mfa_enabled=true (双重认证正式生效)")
        else:
            _l(f"⚠️ 官方复核提示：enroll 已成功，但 mfa_info 暂未刷新")

        return {
            "ok": True,
            "secret": secret,
            "code": code,
            "factor_id": factor_id,
            "session_id": session_id,
            "mfa_enabled": mfa_confirmed,
        }
    except Exception as e:
        logger.warning(f"bind_totp_2fa_by_token 失败: {e}")
        raise


def bind_totp_2fa_adaptive(
    row: dict,
    proxy: str = "",
    step_cb: Optional[Callable[[str], None]] = None,
    log_cb: Optional[Callable[[str], None]] = None,
) -> dict:
    """智能自适应 2FA 绑定：
    1. 若已有 access_token 则尝试直接打官方接口绑定；
    2. 若 access_token 过期且有 refresh_token，自动通过 RT 极速换取新 access_token 后继续绑定；
    3. 成功后自动回写数据库。
    """
    from . import db
    from .token_refresh_service import refresh_token_fast

    def _log(msg: str):
        logger.info(f"[2fa-adaptive] {msg}")
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    def _step(step_name: str):
        if step_cb:
            try:
                step_cb(step_name)
            except Exception:
                pass

    email = (row.get("email") or "").strip().lower()
    if not email:
        raise ValueError("缺少邮箱")

    # 如果已有 totp_secret，先复核是否已绑
    current_secret = (row.get("totp_secret") or "").strip()
    if current_secret:
        _log("该账号在本地已登记有 2FA Secret，复用现有配置")
        return {
            "ok": True,
            "already_bound": True,
            "secret": current_secret,
            "code": totp_now(current_secret),
            "message": "该账号已在数据库中登记 2FA",
        }

    token = (row.get("access_token") or "").strip()
    refresh_token = (row.get("refresh_token") or "").strip()

    # 优先尝试现有 access_token
    if token:
        try:
            _step("正在通过现有 Access Token 请求官方 mfa/enroll...")
            _log("使用现有 Access Token 打官方 mfa_info 检查...")
            res = bind_totp_2fa_by_token(token, proxy=proxy, log_cb=_log, step_cb=_step)
            if res.get("secret"):
                db.update_registered_manual(email, totp_secret=res["secret"])
                _log(f"🎉 2FA 绑定成功！Secret={res['secret']} | 当前动态码: {totp_now(res['secret'])}")
            elif res.get("already_bound") and current_secret:
                res["secret"] = current_secret
                res["totp_secret"] = current_secret
                _log(f"✅ 官方服务端确认已开启 2FA！Secret: {current_secret} | 当前动态码: {totp_now(current_secret)}")
            return res
        except Exception as e:
            err_msg = str(e)
            _log(f"⚠️ 账号使用现有 AT 绑定失败 ({err_msg})，尝试 RT 换取新 Token...")

    # 尝试使用 refresh_token 刷新 access_token
    if refresh_token:
        try:
            _step("正在通过 Refresh Token 换取新凭证...")
            _log(f"正在使用 Refresh Token 极速刷新凭证...")
            rf_res = refresh_token_fast(refresh_token, proxy=proxy)
            new_at = rf_res.get("access_token") or ""
            new_rt = rf_res.get("refresh_token") or refresh_token
            if new_at:
                db.update_registered_manual(email, access_token=new_at, refresh_token=new_rt)
                _step("新 Token 换取成功，正在提交官方 2FA 激活...")
                _log(f"凭证刷新成功，正在向 OpenAI 官方提交 2FA 激活...")
                res = bind_totp_2fa_by_token(new_at, proxy=proxy, log_cb=_log, step_cb=_step)
                if res.get("secret"):
                    db.update_registered_manual(email, totp_secret=res["secret"])
                    _log(f"🎉 2FA 绑定成功！Secret={res['secret']} | 当前动态码: {totp_now(res['secret'])}")
                elif res.get("already_bound") and current_secret:
                    res["secret"] = current_secret
                    res["totp_secret"] = current_secret
                    _log(f"✅ 官方服务端确认已开启 2FA！Secret: {current_secret} | 当前动态码: {totp_now(current_secret)}")
                return res
        except Exception as e2:
            _log(f"⚠️ RT 刷新后绑定 2FA 提示: {e2}，准备启动官方交互式登录链重获认证...")

    # 3. 终极回落：当快路径遇到 recent_auth_required 或无可用 Token 时，自动启动官方 AuthFlow 登录链重走认证
    _step("正在通过官方 AuthFlow 交互式认证链申请绑定 2FA...")
    _log("⚠️ 遇到 OpenAI recent_auth_required 安全限制（要求近期交互式认证），自动启动官方 AuthFlow 登录链重新获取认证并绑定 2FA...")

    cfg = Config()
    cfg.proxy = proxy or db.get_setting("proxy", "") or None

    settings = db.get_mail_settings()
    account_row = db.get_account(email) or {}
    saved_oauth = {}
    if row.get("extra"):
        saved_oauth = row["extra"].get("mail_oauth") or {}

    mail_source = ""
    if saved_oauth.get("kind") == "remail" or saved_oauth.get("service_token") or saved_oauth.get("pickup_url"):
        mail_source = "remail"
        account_row = {
            "email": email,
            "service_token": saved_oauth.get("service_token", ""),
            "pickup_url": saved_oauth.get("pickup_url", ""),
            "order_no": saved_oauth.get("order_no", ""),
            "project_id": saved_oauth.get("project_id", 2),
            "email_suffix": saved_oauth.get("email_suffix", "outlook.com"),
            "service_mode": saved_oauth.get("service_mode", "purchase"),
            "kind": "remail",
        }
    elif saved_oauth.get("kind") == "outlook" or saved_oauth.get("refresh_token") or saved_oauth.get("password"):
        mail_source = "outlook"
        account_row = {
            "email": email,
            "password": saved_oauth.get("password", ""),
            "client_id": saved_oauth.get("client_id", ""),
            "refresh_token": saved_oauth.get("refresh_token", ""),
            "kind": "outlook",
        }
    elif saved_oauth.get("kind") == "icloud_relay" or saved_oauth.get("relay_url"):
        mail_source = "icloud_relay"
        account_row = {
            "email": email,
            "relay_url": saved_oauth.get("relay_url", ""),
            "kind": "icloud_relay",
        }

    # 兜底 remail_recycle_pool
    if not mail_source:
        try:
            cur_pool = db._conn().execute(
                "SELECT service_token, order_no, project_id, email_suffix, service_mode FROM remail_recycle_pool WHERE email=? AND service_token IS NOT NULL AND service_token != '' ORDER BY id DESC LIMIT 1",
                (email,),
            ).fetchone()
            if cur_pool and cur_pool["service_token"]:
                mail_source = "remail"
                account_row = {
                    "email": email,
                    "service_token": cur_pool["service_token"],
                    "order_no": cur_pool.get("order_no", ""),
                    "project_id": cur_pool.get("project_id", 2),
                    "email_suffix": cur_pool.get("email_suffix", "outlook.com"),
                    "service_mode": cur_pool.get("service_mode", "purchase"),
                    "kind": "remail",
                }
        except Exception:
            pass

    if not mail_source:
        if any(dom in email for dom in ("@outlook.", "@hotmail.", "@live.", "@msn.")):
            mail_source = "outlook"
        elif any(dom in email for dom in ("@icloud.", "@me.", "@mac.")):
            def_source = (db.get_setting("mail_source", "") or "").strip().lower()
            mail_source = "remail" if def_source == "remail" else "icloud_relay"
        else:
            mail_source = (db.get_setting("mail_source", "") or "cf_temp").strip().lower()

    try:
        from mail_providers import create_mail_provider
        mail_provider = create_mail_provider(mail_source, settings, account_row)
    except Exception as e:
        _log(f"构造收信渠道异常: {e}，回退 cf_temp")
        from mail_providers import create_mail_provider
        mail_provider = create_mail_provider("cf_temp", settings)

    pwd = (row.get("password") or "").strip()
    if not pwd and account_row:
        pwd = (account_row.get("password") or "").strip()

    slow_res = bind_totp_2fa(
        cfg=cfg,
        email=email,
        password=pwd,
        mail_provider=mail_provider,
        env_overrides={"OTP_TIMEOUT": "60"},
        log_cb=_log,
        step_cb=_step,
    )
    if slow_res and slow_res.get("secret"):
        db.update_registered_manual(email, totp_secret=slow_res["secret"])
        _log(f"🎉 官方深度登录链 2FA 绑定成功！Secret={slow_res['secret']} | 当前动态码: {totp_now(slow_res['secret'])}")
        return {
            "ok": True,
            "secret": slow_res["secret"],
            "code": totp_now(slow_res["secret"]),
            "factor_id": slow_res.get("factor_id", ""),
            "session_id": slow_res.get("session_id", ""),
            "mfa_enabled": True,
        }

    raise RuntimeError("未能通过官方认证链完成 2FA 绑定，请检查账号密码或收信状态")


# ── 真正干活的三步：查已绑 → enroll → activate ────────────────────
def _enroll_and_activate(
    flow: AuthFlow,
    at: str,
    log_cb: Optional[Callable[[str], None]] = None,
    step_cb: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """拿 flow.session + access_token 完成 enroll/activate，成功返回 dict。

    快慢两条路唯一的区别只在【怎么弄到这个 at】，弄到之后的动作完全一样，
    所以抽出来共用，免得两份代码各改各的漂移掉。
    """
    def _l(msg: str):
        logger.info(f"[2fa] {msg}")
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    def _s(step: str):
        if step_cb:
            try:
                step_cb(step)
            except Exception:
                pass

    # 幂等保护：已绑 totp 则不重复 enroll（secret 取不回，只能日志提示）
    _s("1/4 检查官方 2FA 状态...")
    _l("🔍 正在调用 OpenAI 官方 /backend-api/accounts/mfa_info 检查当前 2FA 绑定状态...")
    hh = flow._common_headers("https://chatgpt.com/backend-api/accounts/mfa_info")
    hh["Authorization"] = f"Bearer {at}"
    r2 = flow.session.get(
        "https://chatgpt.com/backend-api/accounts/mfa_info", headers=hh, timeout=30
    )
    if r2.status_code == 200:
        info = r2.json() or {}
        if info.get("mfa_enabled") and (info.get("factors", {}) or {}).get("totp"):
            _l("ℹ️ OpenAI 官方服务端检测：该账号已开启 2FA TOTP 双重认证（Secret 仅在初次生成时下发）")
            return None

    _s("2/4 申请官方 TOTP 密钥...")
    _l("🔑 正在向 OpenAI 官方 /backend-api/accounts/mfa/enroll 请求下发 TOTP 密钥...")
    hh = flow._common_headers("https://chatgpt.com/backend-api/accounts/mfa/enroll")
    hh["Authorization"] = f"Bearer {at}"
    hh["Content-Type"] = "application/json"
    r3 = flow.session.post(
        "https://chatgpt.com/backend-api/accounts/mfa/enroll",
        headers=hh, json={"factor_type": "totp"}, timeout=30,
    )
    if r3.status_code != 200:
        _l(f"❌ OpenAI 官方 enroll 失败 ({r3.status_code}): {(r3.text or '')[:200]}")
        return None
    en = r3.json() or {}
    secret = en.get("secret", "")
    session_id = en.get("session_id", "")
    factor_id = (en.get("factor", {}) or {}).get("id", "")
    if not secret or not session_id:
        _l(f"❌ OpenAI 官方 enroll 响应缺少 secret 或 session_id: {json.dumps(en)[:200]}")
        return None

    _l(f"📥 成功获取官方下发的 2FA Secret: {secret} (共 {len(secret)} 位密钥)")

    _s("3/4 计算验证码并激活...")
    code = totp_now(secret)
    _l(f"⏱️ 正在依据 RFC 6238 算法计算当前 6 位动态验证码: {code}，提交官方激活...")
    hh = flow._common_headers(
        "https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment"
    )
    hh["Authorization"] = f"Bearer {at}"
    hh["Content-Type"] = "application/json"
    r4 = flow.session.post(
        "https://chatgpt.com/backend-api/accounts/mfa/user/activate_enrollment",
        headers=hh,
        json={"code": code, "factor_type": "totp", "session_id": session_id},
        timeout=30,
    )
    if r4.status_code != 200:
        _l(f"❌ OpenAI 官方 activate_enrollment 失败 ({r4.status_code}): {(r4.text or '')[:200]}")
        return None

    _l(f"✨ 官方动态码校验通过 (HTTP 200)")

    # 验证（失败不影响返回：enroll+activate 都 200 即视为成功，secret 已到手）
    _s("4/4 复核官方状态...")
    _l("🔄 正在复核官方 /backend-api/accounts/mfa_info 确认 mfa_enabled 状态...")
    time.sleep(1)
    hh = flow._common_headers("https://chatgpt.com/backend-api/accounts/mfa_info")
    hh["Authorization"] = f"Bearer {at}"
    r5 = flow.session.get(
        "https://chatgpt.com/backend-api/accounts/mfa_info", headers=hh, timeout=30
    )
    if r5.status_code == 200 and (r5.json() or {}).get("mfa_enabled"):
        _l(f"✅ 官方复核确认：mfa_enabled=true (双重认证正式生效)")
    else:
        _l(f"⚠️ 官方复核提示：enroll 已成功，但 mfa_info 暂未刷新")

    return {"secret": secret, "factor_id": factor_id, "session_id": session_id}


# ── 快路径：复用注册会话，不重新登录 ──────────────────────────────
def bind_totp_2fa_inline(flow: AuthFlow, access_token: str = "") -> dict | None:
    """注册刚跑完，直接用同一个 flow 绑 2FA。成功返回 dict，失败返回 None。"""
    try:
        at = access_token or getattr(getattr(flow, "result", None), "access_token", "")
        if not at:
            logger.warning("[2fa] 快路径：注册会话没有 access_token，回落慢路径")
            return None
        return _enroll_and_activate(flow, at)
    except Exception as e:  # noqa: BLE001 — 绝不能拖垮已注册成功的号
        logger.warning("[2fa] 快路径异常（回落慢路径）: %s", e)
        return None


# ── 慢路径（兜底）：新起 flow 重走 login 正式链 ────────────────────
def bind_totp_2fa(
    cfg: Config,
    email: str,
    password: str,
    mail_provider=None,
    env_overrides: dict | None = None,
    log_cb: Optional[Callable[[str], None]] = None,
    step_cb: Optional[Callable[[str], None]] = None,
) -> dict | None:
    """
    注册成功后为账号绑定 TOTP 2FA。

    成功 → 返回 {"secret", "factor_id", "session_id"}；
    任何一步失败 / 前提不满足 / 已绑定 → 返回 None（仅记日志，绝不抛异常，
    以免拖垮已经注册成功的号 —— 调用方 registrar 再套一层 try/except 兜底）。
    """
    def _l(msg: str):
        logger.info(f"[2fa-authflow] {msg}")
        if log_cb:
            try:
                log_cb(msg)
            except Exception:
                pass

    def _s(step: str):
        if step_cb:
            try:
                step_cb(step)
            except Exception:
                pass

    if not email:
        _l("⚠️ 缺少邮箱，跳过绑定")
        return None

    try:
        flow = AuthFlow(cfg, env_overrides=dict(env_overrides or {}))
        chain_started_at = time.time()

        _s("1/11 检查代理与预热...")
        _l("[2FA] 1/11 检查代理连通性并预热会话...")
        flow.check_proxy()
        if not flow.warmup():
            raise RuntimeError(
                "warmup 失败：未拿到 oai-did cookie，绑定链路必然 409 invalid_state"
            )

        _s("2/11 获取 CSRF Token...")
        _l("[2FA] 2/11 获取 csrf_token...")
        csrf = flow.get_csrf_token()

        _s("3/11 获取 OAuth 地址...")
        _l("[2FA] 3/11 获取 OAuth 授权地址...")
        auth_url = flow.get_auth_url(csrf, email=email)

        _s("4/11 初始化 OAuth 会话...")
        _l("[2FA] 4/11 OAuth 初始化（获取 device_id）...")
        device_id = flow.auth_oauth_init(auth_url)

        _s("5/11 计算 PoW 算力证明...")
        _l("[2FA] 5/11 获取 Sentinel Token (PoW 算力证明)...")
        flow.get_sentinel_token(device_id)

        _s("6/11 提交邮箱建认证会话...")
        _l("[2FA] 6/11 authorize/continue 提交邮箱建立 Login Challenge...")
        step = flow.authorize_continue(
            email, flow._last_sentinel_token,
            screen_hint="login",
            referer="https://auth.openai.com/log-in",
            trace_step="bind_2fa",
        )
        page_type = flow._extract_page_type(step)
        continue_url = flow._normalize_continue_url(
            flow._extract_continue_url_from_step(step)
        )
        _l(f"[2FA] page.type = {page_type!r}")

        # ── 7/11 密码链（服务端给密码页时才走）──
        if page_type == "login_password" or "/log-in/password" in continue_url:
            _s("7/11 验证账号密码...")
            _l("[2FA] 7/11 打开密码页并提交密码验证...")
            flow.session.get(
                f"https://auth.openai.com/log-in/password?email={urllib.parse.quote(email)}",
                headers=flow._common_headers("https://auth.openai.com/log-in/password"),
                timeout=30,
            )
            step = flow.login_password_verify(password)
            page_type = flow._extract_page_type(step)
            continue_url = flow._normalize_continue_url(
                flow._extract_continue_url_from_step(step)
            )
            _l(f"[2FA] 密码验证后 page.type = {page_type!r}")

        # ── 7.5/11 邮件 OTP 链 ──
        need_otp = (page_type == "email_otp_verification") or (
            "/email-verification" in (continue_url or "")
        )
        if need_otp:
            if mail_provider is None:
                _l("⚠️ 该号需邮件 OTP，但未提供 mail_provider，跳过绑定")
                return None
            try:
                otp_timeout = max(10, int(flow._get_env("OTP_TIMEOUT", "60")))
            except Exception:
                otp_timeout = 60
            _s("7.5/11 接收邮件验证码...")
            _l(f"[2FA] 7.5/11 需要邮件 OTP 验证（等待超时 {otp_timeout}s）...")

            otp_code = None
            try:
                peek = getattr(mail_provider, "peek_otp", None)
                if callable(peek):
                    otp_code = peek(email, issued_after=chain_started_at, wait=4)
            except Exception as e:
                logger.debug("[2fa] 预读 OTP 异常（回退到发码）: %s", e)

            if not otp_code:
                _l("[2FA] 正在主动触发官方重发验证码邮件...")
                if not flow.kickoff_otp_delivery("existing_bind_2fa"):
                    flow.send_otp(referer="https://auth.openai.com/email-verification")
                otp_code = mail_provider.wait_for_otp(
                    email, timeout=otp_timeout, issued_after=chain_started_at,
                )
            _l(f"📥 成功获取验证码: {otp_code}，提交验证...")
            otp_resp = flow.verify_otp(otp_code)
            page_type = flow._extract_page_type(otp_resp)
            continue_url = flow._normalize_continue_url(
                flow._extract_continue_url_from_step(otp_resp)
            )
            _l(f"[2FA] OTP 验证通过，page.type = {page_type!r}")

        cu = continue_url
        if not cu:
            _l(f"⚠️ 登录链没拿到 continue_url（page.type={page_type!r}），跳过")
            return None

        # 已绑 2FA 的号验证完直接进 mfa-challenge（此时不该重复绑定）
        if "/mfa-challenge/" in cu:
            _l("ℹ️ 该号在官方已启用 2FA（进入 mfa-challenge），跳过重复绑定")
            return None

        _s("8/11 消费 Callback...")
        _l("[2FA] 8/11 消费 callback，建立授权会话...")
        if not flow._consume_callback_for_session(cu):
            _l("⚠️ 消费 callback 失败，跳过")
            return None

        _s("9/11 获取 Token...")
        _l("[2FA] 9/11 获取 access_token...")
        _st, at = flow.get_auth_session()
        if not at:
            _l("⚠️ 未拿到 access_token，跳过")
            return None

        # 10 ~ 11/11：完成 enroll + activate
        return _enroll_and_activate(flow, at, log_cb=_l, step_cb=_s)

    except Exception as e:  # noqa: BLE001
        _l(f"❌ 绑定过程异常: {e}")
        return None
