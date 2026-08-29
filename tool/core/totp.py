"""totp.py — 纯 Python 标准库实现的 RFC 6238 TOTP 双因子动态验证码计算器
=======================================================================
无任何第三方库依赖（无需 pyotp），零环境门槛，支持任意标准 Base32 密钥。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time


def normalize_secret(secret: str) -> str:
    """清洗 TOTP Secret 密钥（去除空格、连字符、转大写、补齐 Base32 填充符）。"""
    s = (secret or "").strip().replace(" ", "").replace("-", "").upper()
    if not s:
        return ""
    # Base32 编码长度必须是 8 的倍数，缺失 '=' 则自动补齐
    pad_len = (8 - (len(s) % 8)) % 8
    return s + ("=" * pad_len)


def get_totp_token(secret: str, digits: int = 6, interval: int = 30, for_time: float | None = None) -> str:
    """计算指定时间或当前时刻的标准 6 位 TOTP 验证码。

    Args:
        secret: Base32 格式的 2FA Secret（如 JBSWY3DPEHPK3PXP）
        digits: 验证码位数（默认 6 位）
        interval: 时间步长秒数（默认 30 秒）
        for_time: 指定时间戳，留空则取当前系统时间

    Returns:
        str: 6 位数字验证码（如 "123456"），计算失败返回空字符串
    """
    clean_s = normalize_secret(secret)
    if not clean_s:
        return ""
    try:
        key = base64.b32decode(clean_s, casefold=True)
    except Exception:
        return ""

    t = int(for_time if for_time is not None else time.time())
    counter = t // interval
    msg = struct.pack(">Q", counter)

    h = hmac.new(key, msg, hashlib.sha1).digest()
    offset = h[-1] & 0x0F
    code_int = struct.unpack(">I", h[offset:offset + 4])[0] & 0x7FFFFFFF
    token = str(code_int % (10 ** digits)).zfill(digits)
    return token


def get_totp_remaining_seconds(interval: int = 30) -> int:
    """获取当前 30 秒 TOTP 窗口剩余秒数（1~30）。"""
    t = int(time.time())
    return interval - (t % interval)
