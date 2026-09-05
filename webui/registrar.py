"""注册 worker：调 auth_flow.run_register，并把日志/状态实时推到队列。

每个注册任务跑在独立线程；通过 `RunLogger` 把 `logging` 记录 + tail 状态推
到队列，前端用 SSE 实时收日志。
"""
from __future__ import annotations

import logging
import queue
import sys
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]  # gpt-outlook-register/
sys.path.insert(0, str(ROOT))

from config import Config  # noqa: E402
from auth_flow import AuthFlow  # noqa: E402
from mail_providers import (  # noqa: E402
    MailProviderError,
    create_mail_provider,
    get_provider_class,
)
from sms_providers import PhoneCallbackController, canonicalize_kind, get_provider_class  # noqa: E402

from . import db  # noqa: E402
from .proxy_util import (  # noqa: E402
    ALL_AVAILABLE_COUNTRIES,
    HOT_COUNTRIES,
    new_proxy_session_id,
    resolve_target_country,
    route_proxy_country,
)

# run_id -> queue of log strings; sentinel = None 表示流结束
_run_queues: dict[str, queue.Queue] = {}
_run_phases: dict[str, dict] = {}
_lock = threading.Lock()
MAX_PHASES_HISTORY = 300


def set_run_phase(run_id: str, phase: str, text: str, percent: int = 0):
    with _lock:
        if len(_run_phases) >= MAX_PHASES_HISTORY and run_id not in _run_phases:
            try:
                oldest_key = next(iter(_run_phases))
                _run_phases.pop(oldest_key, None)
            except Exception:
                pass
        _run_phases[run_id] = {
            "phase": phase,
            "phase_text": text,
            "percent": percent,
            "updated_at": time.time(),
        }
    _emit_status(run_id, "phase", {"phase": phase, "phase_text": text, "percent": percent})


def get_run_phase(run_id: str) -> dict:
    with _lock:
        return _run_phases.get(
            run_id, {"phase": "starting", "phase_text": "正在注册...", "percent": 10}
        )


def _detect_phase_from_log(msg: str) -> tuple[str, str, int] | None:
    """根据日志特征自动提取细粒度注册步骤与进度百分比。"""
    if not msg:
        return None
    if "网络预检" in msg or "检查网络连通性" in msg or "[1/10]" in msg:
        return "network", "网络连通性预检", 15
    if "[2/10]" in msg or "获取 OpenAI 授权地址" in msg:
        return "auth_url", "获取授权地址", 25
    if "[3/10]" in msg or "OAuth 初始化" in msg:
        return "oauth_init", "OAuth 授权初始化", 35
    if "[4/10]" in msg or "Sentinel Token" in msg:
        return "sentinel", "计算人机风控 Token", 45
    if "[5/10]" in msg or "密码注册" in msg:
        return "register_pw", "提交注册密码", 55
    if "[6/10]" in msg or "发送 OTP" in msg or "passwordless OTP" in msg:
        return "otp_sent", "已发送邮箱验证码", 65
    if "[7/10]" in msg or "接收并验证 OTP" in msg or "等待邮件验证码" in msg:
        return "otp_verify", "正在接收并验证 OTP", 75
    if "[8/10]" in msg or "创建账户" in msg or "create_account" in msg:
        return "creating", "创建 ChatGPT 账户", 85
    if "2FA" in msg or "two_factor" in msg:
        if "成功" in msg or "2fa_bound" in msg:
            return "2fa_done", "2FA 绑定成功", 95
        return "binding_2fa", "绑定 TOTP 二步验证", 90
    if "[register] 完成" in msg or "注册完成" in msg:
        return "done", "注册完成", 100
    if "[register] 失败" in msg or "ERROR" in msg:
        return "failed", "注册失败", 100
    return None

# 当前线程正在跑哪个 run。
# ⚠️ 为什么需要这个：QueueLogHandler 是挂在 **root logger** 上的，而 root logger
#    是进程全局的。auto_loop 并发时 N 个 run 各挂一个 handler，每条日志会被
#    广播进**所有** run 的文件和 SSE 流 —— 实测 2026-08-04 三 worker 并发，
#    一个号的记录同时出现在 3 个 .log 里，WebUI 上三个号的日志搅在一起，
#    而 "[4/10] 获取 Sentinel Token..." 这类行不带邮箱，根本分不清是谁的。
#
#    注册链路（auth_flow / mail_providers / sentinel）内部不开任何线程，
#    一个 run 的日志全在自己那条线程上产生，所以线程绑定就能干净切开。
_current_run = threading.local()

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def extract_proxy_country(proxy: str, target_country: str = "") -> str:
    """提取注册目标或代理所属的国家代码（如 BR、DE、GB 等），不再发起外部 IP 探针请求。"""
    import re
    if target_country and target_country.strip():
        return target_country.strip().upper()
    proxy = (proxy or "").strip()
    if not proxy:
        return ""
    try:
        m = re.search(r"(?:-region-|-country-|_country-)([a-zA-Z]{2})", proxy, re.I) or re.search(
            r"-([a-zA-Z]{2})-\d+-\d+", proxy, re.I
        )
        if m and m.group(1):
            return m.group(1).upper()
    except Exception:
        pass
    return ""


# 兼容历史调用别名
def probe_proxy_geo(proxy: str, timeout: float = 6.0) -> dict:
    country = extract_proxy_country(proxy)
    return {"ip": "", "country": country, "city": ""}


class QueueLogHandler(logging.Handler):
    """把 logging 记录扔进 run queue + 写 log 文件。

    只收**本 run 线程**产生的日志，见 emit 里的过滤。
    """

    def __init__(self, run_id: str, log_file: Path):
        super().__init__()
        self.run_id = run_id
        self._fh = open(log_file, "a", encoding="utf-8")
        self.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        ))

    def emit(self, record: logging.LogRecord):
        try:
            # emit 是在**打日志的那条线程**里同步跑的，所以这里读到的就是
            # 日志产生者的 run_id。别人 run 的日志直接丢掉。
            rid = getattr(_current_run, "run_id", None)
            if rid is not None and rid != self.run_id:
                return
            # rid is None = 不属于任何 run（webui 请求线程、启动期日志等）。
            # 这类照旧广播给所有 handler —— 宁可多收也不能丢，日志文件
            # 开头那句 "webui: [run] xxx -> email@..." 就是这么来的。
            msg = self.format(record)
            self._fh.write(msg + "\n")
            self._fh.flush()

            # 语义识别细分步骤
            detected = _detect_phase_from_log(record.getMessage())
            if detected and rid:
                p_code, p_text, p_pct = detected
                with _lock:
                    _run_phases[rid] = {
                        "phase": p_code,
                        "phase_text": p_text,
                        "percent": p_pct,
                        "updated_at": time.time(),
                    }

            q = _run_queues.get(self.run_id)
            if q is not None:
                q.put(msg)
        except Exception:
            pass

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass
        super().close()


def _emit_status(run_id: str, kind: str, payload: dict | str = ""):
    """前端约定：以 `__EVENT__:` 开头的行被解析成 JSON 状态事件。"""
    import json as _json
    q = _run_queues.get(run_id)
    if q is None:
        return
    body = payload if isinstance(payload, dict) else {"message": str(payload)}
    body["kind"] = kind
    q.put("__EVENT__:" + _json.dumps(body, ensure_ascii=False))


# 网络/环境层错误特征：命中任一就把号放回 available（号本身没问题，是环境炸了）
_NETWORK_ERROR_PATTERNS = [
    "tls", "ssl", "sslerror", "connection", "connect error", "timeout", "timed out",
    "proxy", "socks", "dns", "name resolution", "name or service",
    "cloudflare", "just a moment", "403 forbidden",
    "csrf token 获取失败", "csrf token 失败",
    "/sentinel/req", "sentinel /req", "sentinel quickjs",
    "check_proxy 失败", "网络预检查",
    "curl: (35)", "curl: (28)", "curl: (6)", "curl: (7)",
    "remote disconnected", "connection reset", "connection aborted",
    "max retries exceeded",
    "invalid_state",
]


def classify_error(err: str, mail_source: str = "") -> str:
    """分类错误：'network'（环境/代理问题，号无辜）/ 'account'（号本身有问题）/ 'unknown'。

    mail_source 用来问 provider 要不要豁免某些模式 —— 比如 iCloud 中转号
    本来就是买的老号，"已有账号"是正常流程不是失败（见
    MailProvider.accepts_existing_account）。留空则按最严格的规则判。
    """
    s = (err or "").lower()

    account_patterns = [
        "wrong_email_otp_code", "invalid_grant", "imap xoauth2",
        "outlook imap account unusable", "user is authenticated but not connected",
        "outlook refresh failed", "authentication failed", "authenticate failed",
        "outlook otp timeout", "registration_disallowed", "user_already_exists",
        "already exists for this email", "continue_to_login",
        "account_deactivated", "deleted or deactivated", "deactivated",
        "mfa-challenge", "缺少 totp 密钥", "已开启 2fa", "2fa 两步验证",
        "已被官方封禁", "已有账号", "账号被", "refresh_token 失效",
    ]
    if mail_source:
        try:
            exempt = get_provider_class(mail_source).accepts_existing_account
        except MailProviderError:
            exempt = False  # 未知来源 —— 按默认最严格规则走
        # ⚠️ 用 if-in 而不是裸 remove()：上面的模式表将来被人改动/重排后，
        #    remove 抛的 ValueError 会跟 get_provider_class 的错混在同一个
        #    except 里被一起吞掉，豁免静默失效且没人看得出来。
        if exempt and "已有账号" in account_patterns:
            account_patterns.remove("已有账号")

    # 先匹配 account 特征（更具体），避免子串误命中（如 "outlook OTP timeout" 含 "timeout"）
    if any(p in s for p in account_patterns):
        return "account"
    if any(p in s for p in _NETWORK_ERROR_PATTERNS):
        return "network"
    return "unknown"


def _do_register(
    run_id: str,
    account: dict,
    options: dict,
    log_file: Path,
):
    """实际注册任务。

    options:
        want_access_token: bool
        want_session_token: bool
        want_refresh_token: bool
        proxy: Optional[str]
        otp_timeout: int
        allow_existing_login: bool
    """
    # 先认领本线程，再挂 handler —— 顺序不能反：中间要是有日志产生，
    # 没打标记的话会被广播到其他并发 run 的日志里去。
    _current_run.run_id = run_id

    handler = QueueLogHandler(run_id, log_file)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    # 第一次需要的话提到 INFO 级别
    if root_logger.level > logging.INFO or root_logger.level == 0:
        root_logger.setLevel(logging.INFO)

    email = account["email"]
    # 提前读取，优先用 options 传入的 mail_source，其次 account['kind']，最后回退全局配置
    mail_source = (options.get("mail_source") or account.get("kind") or db.get_setting("mail_source", "cf_temp")).strip().lower()
    # 要不要操作号池（mark_done / mark_failed / release）由 provider 声明的
    # pooled 决定。未知 kind 时保守当池化处理 —— 号池里真有这行的话
    # 至少不会漏掉状态回写，把号永远卡在 in_use。
    try:
        is_pooled = get_provider_class(mail_source).pooled
    except MailProviderError:
        is_pooled = True

    try:
        # 本次注册专属的配置覆盖。
        # ⚠️ 以前是写 os.environ + finally 还原，但 auto_loop 并发跑多个 worker，
        #    os.environ 是**进程全局**的：A 设的 OTP_TIMEOUT/WEBUI_ALLOW_LOGIN 会被
        #    B 读到，B 跑完还原成 A 之前的值，A 后半程就用上别人的配置了。
        #    现在整个 dict 直接传给 AuthFlow，只挂在实例上，谁都污染不到谁。
        env_overrides = {}
        # outlook 接码邮箱常被 OpenAI 走 passwordless_signup 流程（新号收码而非设密码），
        # auth_flow 会误判为"已有账号"分支 → 不设 WEBUI_ALLOW_LOGIN 会 fast-fail。
        # 单号 WebUI 场景下 fast-fail 没意义（批量跑才需要"跳过被识别的号"），故强制 ON。
        env_overrides["WEBUI_ALLOW_LOGIN"] = "1"
        env_overrides["OTP_TIMEOUT"] = str(int(options.get("otp_timeout") or 180))
        # 自动设置登录密码开关（支持前端开关控制，Remail 渠道强制开启）
        want_password = bool(options.get("want_password", True))
        if mail_source == "remail":
            want_password = True
            options["want_password"] = True
            options["want_2fa"] = True
            logging.getLogger("registrar").info(
                "[register] 🔒 Remail 短效邮箱安全策略生效：强制自动设置强随机密码 + 自动绑定 2FA TOTP（保障邮箱失效后凭 账密+2FA 终身登录）"
            )

        env_overrides["WANT_PASSWORD"] = "1" if want_password else "0"
        # 默认不抢跑 Codex OAuth（避免注册 1 秒内触发自动化工具特征）
        want_refresh = bool(options.get("want_refresh_token", False))
        if not want_refresh:
            env_overrides["SKIP_OAUTH_TOKEN_EXCHANGE"] = "1"
            env_overrides["OAUTH_CODEX_RT_EXCHANGE"] = "0"
            env_overrides["OAUTH_CODEX_RT_BEFORE_CALLBACK"] = "0"

        # ─ 目标代理国家与动态 Session 路由 ─
        raw_target_country = (options.get("proxy_country") or options.get("target_country") or "").strip().upper()
        target_country = resolve_target_country(raw_target_country)

        raw_proxy = (options.get("proxy") or "").strip()
        final_proxy = raw_proxy

        # ─ 健康度换国：目标国家的 (代理模板, 国家) 组合已被拉黑 → 自动换国 ─
        # 优先换到同模板下未拉黑且死亡率最低的国家；候选全黑就保持原选择
        # （固定国家是主人的显式意图，只有确实有更优出口时才换，换了必留日志）。
        if raw_proxy and target_country:
            try:
                from .db import is_combo_blacklisted, pick_healthy_country
                if is_combo_blacklisted(raw_proxy, target_country):
                    candidates = HOT_COUNTRIES if raw_target_country in ("RANDOM_HOT", "HOT", "RANDOM") else (
                        ALL_AVAILABLE_COUNTRIES if raw_target_country in ("RANDOM_ALL", "ALL") else ALL_AVAILABLE_COUNTRIES
                    )
                    alt = pick_healthy_country(raw_proxy, candidates)
                    if alt and alt != target_country:
                        logging.getLogger("registrar").warning(
                            f"[register] 出口 {target_country} 已被健康度拉黑，自动切换到 {alt}"
                        )
                        target_country = alt
            except Exception as _e:
                logging.getLogger("registrar").debug(f"[register] 健康度换国检查跳过: {_e}")

        if target_country:
            env_overrides["TARGET_COUNTRY"] = target_country

        if raw_proxy and target_country:
            routed = route_proxy_country(raw_proxy, target_country, new_proxy_session_id())
            if routed != raw_proxy:
                rotate_tag = f" (智能轮换自 {raw_target_country})" if raw_target_country != target_country else ""
                logging.getLogger("registrar").info(
                    f"[register] 目标注册国家: {target_country}{rotate_tag}，已自动重写代理并生成独立会话"
                )
            final_proxy = routed

        cfg = Config()
        cfg.proxy = final_proxy or None

        # ─ 邮箱来源路由 ─
        # 原来是 if cf_temp / else outlook 的写死分支，加一种邮箱就得回来改。
        # 现在交给注册表工厂：provider 自己从 settings + account 里取需要的字段。
        mail = create_mail_provider(mail_source, db.get_mail_settings(), account)
        logging.getLogger("registrar").info(
            f"[register] 邮箱来源: {mail_source} ({mail.display_name})"
        )

        # ─ 2FA 绑定钩子：插在「拿到 session」和「Codex 授权」之间 ─
        #   主人指定的顺序：注册完 → 绑 2FA → Codex 授权 → 接码。
        #   2FA 必须有 access_token 才能打 mfa/enroll，而 at 只能从 get_auth_session 拿，
        #   所以这是唯一「已有 at 且 Codex 还没跑」的位置（见 auth_flow.py 那处注释）。
        #   钩子里绑成了就把结果存进 _tfa_box，run_register 返回后直接取，不再重绑。
        _tfa_box: dict = {}

        def _bind_2fa_hook(_flow, at: str) -> None:
            # ⚠️ 这里**不查密码**。快路径 bind_totp_2fa_inline 只拿 access_token 打
            #    mfa_info / enroll / activate，全程不碰密码（two_factor.py:153）。
            #    以前拿 flow.result.password 当门禁，把**重跑的老号全挡在门外**：
            #    老号被 OpenAI 认成已有账号 → 本轮不走 register_password →
            #    内存里密码是空的（真密码在库里，靠下面那段回读补），于是 at 明明齐活
            #    也绑不上（实测一个重跑的老号：at 长度 1762 齐活，却被跳过）。
            from .two_factor import bind_totp_2fa_inline
            info = bind_totp_2fa_inline(_flow, at)
            if info and info.get("secret"):
                _tfa_box.update(info)

        def _account_callback_for_flow(email: str) -> dict:
            """从数据库加载账号凭证（密码和 totp_secret）供 AuthFlow 登录时使用。

            用于既有账号登录场景：当服务端返回 mfa-challenge 时，AuthFlow 需要
            totp_secret 来计算 6 位动态码完成 2FA 验证。
            """
            try:
                data = db.get_registered(email)
                if data:
                    return {
                        "password": data.get("password", ""),
                        "totp_secret": data.get("totp_secret", ""),
                    }
            except Exception as e:
                logging.getLogger("registrar").warning(f"[register] account_callback 异常: {e}")
            return {}

        def _on_email_assigned_hook(assigned_email: str, meta: dict) -> None:
            nonlocal email
            email = assigned_email
            db.update_run_email(run_id, assigned_email)
            is_rec = meta.get("is_recycled", False)
            tag = " [♻️ 复用已购]" if is_rec else " [💰 全新购号]"
            logging.getLogger("registrar").info(f"[register] 邮箱已就绪: {assigned_email}{tag}")
            _emit_status(run_id, "email_assigned", {
                "email": assigned_email,
                "is_recycled": is_rec,
                "order_no": meta.get("order_no", ""),
                "expires_at": meta.get("expires_at", 0.0),
            })

        flow = AuthFlow(
            cfg,
            sms_callback=_build_sms_callback(run_id),
            env_overrides=env_overrides,
            on_password=_save_password_early,
            on_session_ready=_bind_2fa_hook if options.get("want_2fa") else None,
            account_callback=_account_callback_for_flow,
            on_email_assigned=_on_email_assigned_hook,
        )
        _emit_status(run_id, "phase", {"phase": "starting", "email": email})
        logging.getLogger("registrar").info(f"[register] 开始: {email}")

        partial = False
        d: dict
        try:
            result = flow.run_register(mail)
            d = result.to_dict()
        except RuntimeError as e:
            # 部分凭证也算成功（OTP 验证通过 + create_account 成功 → flow.result 有 token）
            d = flow.result.to_dict()
            need_access = options.get("want_access_token", True)
            need_session = options.get("want_session_token", True)
            need_refresh = options.get("want_refresh_token", True)
            # 用户勾选的凭证全拿到 → 算正常完成（不视为 partial）
            wanted_ok = (
                (not need_access or d.get("access_token"))
                and (not need_session or d.get("session_token"))
                and (not need_refresh or d.get("refresh_token"))
            )
            has_any = bool(
                d.get("access_token") or d.get("refresh_token") or d.get("session_token")
            )
            if wanted_ok and has_any:
                logging.getLogger("registrar").warning(
                    f"[register] 流程末段异常但用户勾选的凭证已齐: {e}"
                )
            elif has_any:
                partial = True
                logging.getLogger("registrar").warning(
                    f"[register] 部分凭证 (缺用户勾选的某项): {e}"
                )
            else:
                raise

        # ─ 用户选项过滤：未勾选的字段从结果里抹掉，DB 只存用户想要的
        full = d
        d = {
            "email": full.get("email", ""),
            "password": full.get("password", ""),
        }
        if options.get("want_access_token", True):
            d["access_token"] = full.get("access_token", "")
        if options.get("want_session_token", True):
            d["session_token"] = full.get("session_token", "")
            d["cookie_header"] = full.get("cookie_header", "")  # 同样是浏览器注入用
        if options.get("want_refresh_token", True):
            d["refresh_token"] = full.get("refresh_token", "")
            d["id_token"] = full.get("id_token", "")

        # ─ 密码回读：必须在 2FA 之前 ─
        # ⚠️ d 是**本轮内存里**的结果，它不一定知道这个号有密码：
        #    重跑一个之前设过密码的邮箱时，OpenAI 会认成已有账号 → passwordless_login
        #    → register_password 根本不执行 → d["password"] 是空的，
        #    但上一轮 save_password_early 存的密码还在库里。
        #    两个下游都要它：① 2FA 慢路径要用密码重走 login 链；
        #    ② 前端 done 事件 `v-if="lastRunResult.password"` 判空会把密码行
        #       连同两个复制按钮一起藏掉，主人会以为密码丢了。
        #    以前这段在 2FA **之后**，于是老号在 2FA 眼里永远"无密码"→ 被跳过。
        #    只在 d 里密码为空时查一次，正常路径零额外开销。
        if not (d.get("password") or "").strip():
            try:
                _saved = db.get_registered(d.get("email") or "")
                _pw = ((_saved or {}).get("password") or "").strip()
                if _pw:
                    d["password"] = _pw
                    logging.getLogger("registrar").info(
                        "[register] 本轮未设密码，沿用库中已存密码（上一轮 register_password 留下的）"
                    )
            except Exception as e:
                logging.getLogger("registrar").warning(f"[register] 回读已存密码失败: {e}")

        # 若用户勾选了设置密码，但该号通过 passwordless_signup 注册未设密码，则自动通过官方协议补设
        if want_password and not (d.get("password") or "").strip():
            try:
                logging.getLogger("registrar").info(
                    f"[register] 该账号为免密注册，正在调用 OpenAI 官方协议全自动补设登录密码..."
                )
                from .official_password import official_set_account_password
                used_proxy_for_pwd = getattr(cfg, "proxy", "") or options.get("proxy") or ""
                # 传递当前已持有的真实 mail 实例，保障包含完整的 serviceToken 与取件凭证
                res_pwd = official_set_account_password(
                    email=d.get("email"),
                    proxy=used_proxy_for_pwd,
                    timeout=min(int(options.get("otp_timeout") or 60), 60),
                    mail_provider=mail,
                )
                if res_pwd.get("password"):
                    d["password"] = res_pwd["password"]
                    logging.getLogger("registrar").info(
                        f"[register] ✅ 官方登录密码设置成功: {d['password']}"
                    )
            except Exception as e:
                logging.getLogger("registrar").warning(
                    f"[register] 自动补设官方密码异常（账号已注册成功，仅未设密）: {e}"
                )

        # ─ 可选：绑定 TOTP 2FA（仅用户勾选 want_2fa 时才跑） ─
        #   正常情况上面的 on_session_ready 钩子已经在【Codex 授权之前】绑完了，
        #   这里只是兜底：钩子没跑到（run_register 中途抛异常走 partial 分支、
        #   或那时 access_token 还是空）时再补一次。
        #   兜底本身也是先快后慢两条路（见 two_factor.py 模块头）：
        #     快 bind_totp_2fa_inline —— 直接复用刚跑完注册的 flow + access_token，
        #        6.2s 搞定，零 PoW 零邮件（实测 2026-08-08 <测试号>@<自建域>
        #        四个请求全 200，mfa_enabled=true）。
        #     慢 bind_totp_2fa —— 新起 AuthFlow 重走 login 正式链，约 40s + 一次 PoW
        #        + 一封验证码邮件。只在快路径没成时兜底。
        #   失败仅告警、绝不废掉已注册成功的号；secret 一次性下发，成功即随 d 落库+推前端。
        #   ⚠️ 入口条件**不查密码**：快路径只要 access_token。密码只是慢路径
        #      （重走 login 链）的前提，所以判断挪到回落那一步再做。
        if options.get("want_2fa"):
            _emit_status(run_id, "phase", {"phase": "binding_2fa", "email": d.get("email")})
            try:
                from .two_factor import bind_totp_2fa, bind_totp_2fa_inline
                # 钩子（Codex 授权之前那次）已经绑好就直接用，别再打一遍 enroll
                tinfo = dict(_tfa_box) if _tfa_box.get("secret") else None
                if not tinfo:
                    tinfo = bind_totp_2fa_inline(flow, full.get("access_token", ""))
                if not (tinfo and tinfo.get("secret")):
                    # 慢路径要拿密码重登一次，没密码就只能到此为止
                    if (d.get("password") or "").strip():
                        logging.getLogger("registrar").info(
                            "[register] 2FA 快路径未成，回落重走登录链..."
                        )
                        tinfo = bind_totp_2fa(
                            cfg, d.get("email", ""), d.get("password", ""),
                            mail_provider=mail, env_overrides=env_overrides,
                        )
                    else:
                        logging.getLogger("registrar").warning(
                            "[register] 2FA 快路径未成，且该号无密码（库里也没有），"
                            "慢路径走不了，跳过绑定"
                        )
                if tinfo and tinfo.get("secret"):
                    d["totp_secret"] = tinfo["secret"]
                    d["totp_factor_id"] = tinfo.get("factor_id", "")
                    logging.getLogger("registrar").info(
                        f"[register] 2FA 绑定成功 email={d.get('email')}"
                    )
                    _emit_status(run_id, "phase", {"phase": "2fa_bound", "email": d.get("email")})
                else:
                    logging.getLogger("registrar").warning(
                        "[register] 2FA 绑定未成功（账号仍有效，仅未绑 2FA）"
                    )
            except Exception as e:
                logging.getLogger("registrar").warning(
                    f"[register] 2FA 绑定异常（账号仍有效）: {e}"
                )
        # ─ 记录本次注册出口国家（不再发起外部网络探针，直接使用目标国家或代理地区） ─
        used_proxy_str = getattr(cfg, "proxy", "") or options.get("proxy") or ""
        d["reg_country"] = extract_proxy_country(used_proxy_str, target_country)
        # 存档所用代理（随 extra_json 落库）：验死反哺用 —— 号事后被封时
        # 能反查出是哪个代理的号，proxy_health 据此自动拉黑脏 IP。
        if used_proxy_str:
            d["reg_proxy"] = used_proxy_str
        d["reg_city"] = ""
        d["reg_ip"] = ""
        if d.get("reg_country"):
            logging.getLogger("registrar").info(
                f"[register] 注册出口国家: {d['reg_country']}"
            )

        if target_country:
            d["target_country"] = target_country

        # 终身绑定邮箱底层取件凭证（包含 Remail 购买凭证、微软 OAuth 凭证、iCloud 中转凭证）
        if mail_source == "remail" or getattr(mail, "kind", "") == "remail":
            d["mail_oauth"] = {
                "kind": "remail",
                "service_token": getattr(mail, "current_token", ""),
                "pickup_url": getattr(mail, "pickup_url", ""),
                "order_no": getattr(mail, "current_order_no", ""),
                "project_id": getattr(mail, "project_id", 2),
                "email_suffix": getattr(mail, "email_suffix", "icloud.com"),
                "service_mode": getattr(mail, "service_mode", "purchase"),
                "receive_until": getattr(mail, "current_receive_until", ""),
                "expires_at": getattr(mail, "current_expires_at", 0.0),
            }
        elif is_pooled and account:
            d["mail_oauth"] = {
                "client_id": account.get("client_id", ""),
                "refresh_token": account.get("refresh_token", ""),
                "password": account.get("password", ""),
                "kind": account.get("kind", mail_source),
                "relay_url": account.get("relay_url", ""),
            }

        # 落库（密码已在 2FA 之前回读补齐，这里 d 里该有的都有了）
        db.save_registered(d)
        # Remail 邮箱已成功消费
        if mail_source == "remail":
            db.mark_remail_consumed(d.get("email") or getattr(mail, "current_email", ""))

        # 非池化 provider 的 email 是虚拟占位（xxx_placeholder_N@placeholder.local），
        # 号池里根本没这行，不能去 mark。判据用 provider 的 pooled，不写死 kind。
        if is_pooled:
            db.mark_done(email)

        # ─ 可选：导出到 CPA / SUB2API 面板（仅勾选启用时才执行） ─
        _try_export_to_panels(run_id, d)

        result_summary = {
            "email": d.get("email"),
            # 密码走明文推给前端：token 只给长度是因为太长且必须点按钮复制，
            # 但密码是随机 16 位、用户注册完第一件事就是拿去登录，
            # 藏在「查看凭证」弹窗里等于每次都要多点两下。
            # 这是本机自用工具，SSE 只发给本地浏览器，不外传。
            "password": d.get("password") or "",
            "access_token_len": len(d.get("access_token") or ""),
            "session_token_len": len(d.get("session_token") or ""),
            "refresh_token_len": len(d.get("refresh_token") or ""),
            # 2FA secret 一次性下发、服务端取不回，明文推前端让用户当场导入验证器
            # （理由同密码；本机自用工具，SSE 只发本地浏览器）。未绑则为空串。
            "totp_secret": d.get("totp_secret") or "",
            "partial": partial,
        }
        _emit_status(run_id, "done", result_summary)
        logging.getLogger("registrar").info(
            f"[register] 完成 email={d.get('email')} "
            f"pw={d.get('password') or '(无)'} "
            f"at={result_summary['access_token_len']} "
            f"st={result_summary['session_token_len']} "
            f"rt={result_summary['refresh_token_len']}"
        )
        db.finish_run(run_id, "done")

    except Exception as e:
        err = str(e)
        category = classify_error(err, mail_source)
        logging.getLogger("registrar").error(f"[register] 失败 (category={category}): {err}")
        # 清理可能残留的未完成无凭证脏数据，避免污染注册结果列表
        try:
            db.cleanup_pending_registered(email)
        except Exception:
            pass
        # ⚠️ 密码是在 register_password 里现生成的，只活在内存里。
        #    走到这里说明 save_registered 没执行过 —— 但 POST user/register 可能**已经成功**，
        #    OpenAI 那边账号连同这个密码已经建好了，只是后续步骤（发码/验证/建账户）挂了。
        #    不打出来的话这个号就成了谁也登不进去的孤儿。这里只写日志不落库，
        #    避免把没有任何 token 的半成品塞进「注册结果」表里。
        try:
            _pw = (flow.result.password or "").strip()
            if _pw and "account_deactivated" not in err and "deleted or deactivated" not in err and "已被官方封禁" not in err:
                logging.getLogger("registrar").error(
                    f"[register] 该号已生成密码，请自行留存: {flow.result.email or email} / {_pw}"
                )
        except Exception:
            pass  # flow 还没建出来（异常发生在 AuthFlow 之前），没密码可救
        if category != "account":
            logging.getLogger("registrar").error(traceback.format_exc())
        # Remail 处理：如果账号已被 OpenAI 判定为已有/被占，彻底废弃；仅临时网络问题回收进暂存复用池
        is_user_exists = "user_already_exists" in err.lower() or "already exists" in err.lower()
        if is_user_exists:
            bought_email = getattr(mail, "current_email", "") or email
            if bought_email:
                try:
                    db.discard_remail_recycled(bought_email, reason="OpenAI 报告 user_already_exists (母账号已存在)")
                except Exception:
                    pass
        elif mail_source == "remail":
            try:
                bought_email = getattr(mail, "current_email", "")
                bought_token = getattr(mail, "current_token", "")
                if bought_email and bought_token and "placeholder" not in bought_email:
                    db.push_remail_recycled(
                        email=bought_email,
                        service_token=bought_token,
                        order_no=getattr(mail, "current_order_no", ""),
                        project_id=getattr(mail, "project_id", 2),
                        email_suffix=getattr(mail, "email_suffix", "icloud.com"),
                        service_mode=getattr(mail, "service_mode", "purchase"),
                        receive_until=getattr(mail, "current_receive_until", ""),
                        expires_at=getattr(mail, "current_expires_at", 0.0),
                    )
            except Exception as _re_err:
                logging.getLogger("registrar").debug(f"[remail] 暂存未用邮箱异常: {_re_err}")

        # 非池化 provider 没有号池记录，不操作
        if is_pooled:
            if category == "network":
                db.release_unused(email)
                logging.getLogger("registrar").warning(
                    f"[register] {email} 判定为网络/环境错误，号已 release 回 available"
                )
            else:
                db.mark_failed(email, f"[{category}] {err}")
        db.finish_run(run_id, "failed", err, category=category)
        _emit_status(run_id, "error", {"message": err, "category": category})

    finally:
        # env 覆盖现在只挂在 AuthFlow 实例上，随实例一起回收，无需还原。
        # 关闭 handler
        try:
            root_logger.removeHandler(handler)
            handler.close()
        except Exception:
            pass
        q = _run_queues.get(run_id)
        if q is not None:
            q.put(None)  # sentinel: 流结束
        # 线程标记清掉。理论上线程跑完就回收了，但 threading.local 是绑在
        # 线程对象上的，万一以后换成线程池复用线程，残留的 run_id 会让下一个
        # 任务的日志全被投递到上一个 run 的（已关闭的）文件里去。
        _current_run.run_id = None


def _try_export_to_panels(run_id: str, cred: dict) -> None:
    """注册完成后可选地把凭证导出到 CPA / SUB2API 面板。

    - 任一目标的"启用"开关关闭时,该目标跳过(不发请求);两者都未启用时整段 no-op。
    - 任何异常都不抛,只 emit 日志/状态(不影响注册主流程)。
    """
    try:
        cfg = db.get_export_internal_config()
    except Exception as e:
        logging.getLogger("registrar").warning(f"[export] 读取配置失败: {e}")
        return

    cpa_enabled = bool(cfg.get("cpa", {}).get("enabled"))
    sub2api_enabled = bool(cfg.get("sub2api", {}).get("enabled"))
    if not (cpa_enabled or sub2api_enabled):
        return  # 用户没勾选任何目标 → 完全不执行

    from . import exporter  # 懒 import,避免未启用时强依赖

    explog = logging.getLogger("registrar")

    def _log(msg: str, level: str = "info") -> None:
        if level == "error":
            explog.error(f"[export] {msg}")
        elif level == "warn":
            explog.warning(f"[export] {msg}")
        else:
            explog.info(f"[export] {msg}")
        try:
            _emit_status(run_id, "phase", {"phase": "export", "message": msg, "level": level})
        except Exception:
            pass

    try:
        results = exporter.run_exports(
            cred,
            cpa_cfg=cfg.get("cpa") if cpa_enabled else None,
            sub2api_cfg=cfg.get("sub2api") if sub2api_enabled else None,
            log_fn=_log,
        )
    except Exception as e:
        _log(f"导出整体异常: {e}", "error")
        return

    # 汇总成一个事件给前端
    summary = {}
    if results.get("cpa") is not None:
        summary["cpa"] = {"ok": bool(results["cpa"].get("ok")),
                          "message": results["cpa"].get("message") or results["cpa"].get("error") or ""}
    if results.get("sub2api") is not None:
        summary["sub2api"] = {"ok": bool(results["sub2api"].get("ok")),
                              "message": results["sub2api"].get("message") or results["sub2api"].get("error") or ""}
    try:
        _emit_status(run_id, "phase", {"phase": "export_done", "summary": summary})
    except Exception:
        pass


def _save_password_early(email: str, password: str) -> None:
    """AuthFlow 的 on_password 回调：密码在 OpenAI 侧一生效就落盘。

    以前密码只在流程**全部**跑通后才随 save_registered 一起入库，
    中间任何一步失败（实测最常见的是 OTP 超时）密码就只剩一行 ERROR 日志兜底 ——
    换台机器、日志轮转、或者干脆没人去翻，号就废了。

    这里存的是"有密码、无凭证"的半成品行，跑通后 save_registered 会用
    同一个 email 主键覆盖补全，不会多出一行对不上的记录。
    """
    log = logging.getLogger("registrar")
    try:
        db.save_password_early(email, password)
        log.info(f"[register] 密码已落盘: {email}（凭证待补）")
    except Exception as e:
        # 落盘失败不能影响注册；下面 except 里那行 ERROR 日志仍然是兜底
        log.warning(f"[register] 密码落盘失败，仅剩日志兜底: {e}")


def _build_sms_callback(run_id: str) -> Optional[PhoneCallbackController]:
    """根据 webui 配置创建 SMS 接码 controller。

    未启用接码或未配置 API key 时返回 None，flow 会回退到环境变量路径。
    log_fn 把租号/等码的状态推到 SSE 流，前端可见。
    """
    cfg = db.get_sms_internal_config()
    if not cfg.get("sms_enabled"):
        return None
    kind = canonicalize_kind(cfg.get("sms_provider") or "smsbower") or "smsbower"
    try:
        p_cls = get_provider_class(kind)
    except Exception as e:
        logging.getLogger("registrar").warning(f"[sms] 未知接码渠道 {kind}: {e}")
        return None
    api_key = (cfg.get("sms_api_key") or "").strip()
    if p_cls.needs_api_key and not api_key:
        logging.getLogger("registrar").warning("[sms] 已启用接码但未配置 sms_api_key，跳过")
        return None

    smslog = logging.getLogger("registrar")

    def _log(msg: str) -> None:
        # 既写日志、又通过 _emit_status 推 phase 事件给前端
        smslog.info(f"[sms] {msg}")
        try:
            _emit_status(run_id, "phase", {"phase": "sms", "message": msg})
        except Exception:
            pass

    try:
        return PhoneCallbackController(
            provider_key=kind,
            config=cfg,
            service=cfg.get("sms_service") or "openai",
            country=cfg.get("sms_country") or "52",
            log_fn=_log,
            auto_select_country=bool(cfg.get("sms_auto_country")),
        )
    except Exception as e:
        smslog.warning(f"[sms] 创建接码 controller 失败: {e}")
        return None


def start_registration(account: dict, options: dict) -> str:
    """启动一次注册任务，返回 run_id。"""
    run_id = uuid.uuid4().hex[:12]
    log_file = LOG_DIR / f"{run_id}.log"
    db.create_run(run_id, account["email"], str(log_file))

    q: queue.Queue = queue.Queue()
    with _lock:
        _run_queues[run_id] = q

    th = threading.Thread(
        target=_do_register,
        args=(run_id, account, options, log_file),
        daemon=True,
        name=f"register-{run_id}",
    )
    th.start()
    return run_id


def get_run_queue(run_id: str) -> Optional[queue.Queue]:
    return _run_queues.get(run_id)


def remove_run_queue(run_id: str) -> None:
    with _lock:
        _run_queues.pop(run_id, None)
