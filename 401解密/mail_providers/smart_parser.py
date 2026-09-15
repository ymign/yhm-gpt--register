"""号池智能格式兼容与容错解析引擎 (Smart Account Parser)

功能特性：
1. 自动剥离首尾无效字符：BOM 头、不可见控制字符、全角空格、行首行号序号 (如 1. / 1: / [1])、首尾引号等；
2. 自动兼容任意分隔符：----、---、--、制表符 \\t、竖线 |、逗号 ,、分号 ;、冒号 :、连续空格；
3. 智能语义嗅探与乱序自适应：
   - 无论邮箱、密码、Client ID、Refresh Token 处于第几列，均可通过正则与特征自动精准识别并归位；
   - 自动识别 iCloud Relay URL / 自定义中转链接并归类为中转协议；
4. 记录每行的识别方式与置信度，输出清晰的格式分析标签。
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Optional


# 邮箱正则：严格匹配合法标准邮箱
EMAIL_RE = re.compile(
    r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
)

# 嵌入文本中的邮箱提取正则
EMAIL_EXTRACT_RE = re.compile(
    r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+"
)

# GUID / UUID Client ID 正则 (如 Microsoft 固定 OAuth Client ID)
UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# URL 正则
URL_RE = re.compile(r"^https?://[^\s/$.?#].[^\s]*$", re.IGNORECASE)

# 行首序号正则 (如 "1. ", "1: ", "1、", "[1] ", "#1 ")
LINE_PREFIX_RE = re.compile(
    r"^(\d+[\.\:\、\)\-\]\s]+|\[\d+\]\s*|\#\d+\s*)"
)


def clean_raw_line(line: str) -> str:
    """清理整行的不可见字符、BOM头、首尾引号及序号前缀。"""
    if not line:
        return ""
    # 去除 BOM 头和常见不可见控制字符
    s = line.replace("﻿", "").replace("​", "").replace("　", " ")
    s = s.strip()
    if not s or s.startswith("#"):
        return ""
    # 去除行首序号 (如 1. 2: [3])
    s = LINE_PREFIX_RE.sub("", s).strip()
    return s


def split_line_tokens(raw_line: str) -> tuple[list[str], str]:
    """智能尝试多种分隔符进行切分，返回切分后的 token 列表与所匹配的分隔符说明。"""
    line = clean_raw_line(raw_line)
    if not line:
        return [], ""

    # 1. 优先尝试 4 连 / 3 连 / 2 连中划线 "----", "---", "--"
    for dash_sep in ("----", "---", "--"):
        if dash_sep in line:
            parts = [p.strip().strip("\"'“”‘’") for p in line.split(dash_sep)]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                return parts, f"中划线 ({dash_sep})"

    # 2. 尝试制表符 \t (TSV 格式)
    if "\t" in line:
        parts = [p.strip().strip("\"'“”‘’") for p in line.split("\t")]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return parts, "制表符 (Tab)"

    # 3. 尝试竖线 |
    if "|" in line:
        parts = [p.strip().strip("\"'“”‘’") for p in line.split("|")]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return parts, "竖线 (|)"

    # 4. 尝试分号 ;
    if ";" in line:
        parts = [p.strip().strip("\"'“”‘’") for p in line.split(";")]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return parts, "分号 (;)"

    # 5. 尝试逗号 , (CSV 格式)
    if "," in line:
        parts = [p.strip().strip("\"'“”‘’") for p in line.split(",")]
        parts = [p for p in parts if p]
        if len(parts) >= 2:
            return parts, "逗号 (CSV)"

    # 6. 尝试冒号 : (注意：如果是 http:// 或 https://，不要切坏 scheme)
    if ":" in line and not line.lower().startswith(("http://", "https://")):
        # 保护 http(s)://
        temp_line = re.sub(r"(https?):", r"\1__COLON__", line, flags=re.IGNORECASE)
        if ":" in temp_line:
            raw_parts = temp_line.split(":")
            parts = [p.replace("__COLON__", ":").strip().strip("\"'“”‘’") for p in raw_parts]
            parts = [p for p in parts if p]
            if len(parts) >= 2:
                return parts, "冒号 (:)"

    # 7. 尝试连续空格切分 (Space-separated)
    space_parts = [p.strip().strip("\"'“”‘’") for p in re.split(r"\s+", line)]
    space_parts = [p for p in space_parts if p]
    if len(space_parts) >= 2:
        return space_parts, "空格分隔"

    # 单 token（可能只是一个纯邮箱）
    return [line.strip().strip("\"'“”‘’")], "单字段"


def parse_smart_account_line(line: str, default_kind: str = "outlook") -> dict[str, Any]:
    """智能解析单行账号凭据，支持任意乱序自适应归类。

    返回值结构：
    {
        "ok": bool,
        "email": str,
        "password": str,
        "client_id": str,
        "refresh_token": str,
        "relay_url": str,
        "kind": str,
        "raw_line": str,
        "detected_format": str,
        "error": str,
    }
    """
    raw_cleaned = clean_raw_line(line)
    if not raw_cleaned:
        return {
            "ok": False,
            "email": "",
            "password": "",
            "client_id": "",
            "refresh_token": "",
            "relay_url": "",
            "kind": default_kind,
            "raw_line": line,
            "detected_format": "空行",
            "error": "空行或注释行",
        }

    tokens, sep_name = split_line_tokens(raw_cleaned)
    if not tokens:
        return {
            "ok": False,
            "email": "",
            "password": "",
            "client_id": "",
            "refresh_token": "",
            "relay_url": "",
            "kind": default_kind,
            "raw_line": line,
            "detected_format": "无法切分",
            "error": "无法解析有效字段",
        }

    email = ""
    password = ""
    client_id = ""
    refresh_token = ""
    relay_url = ""
    remaining_tokens = []

    # 阶段 1: 优先嗅探 Email 与 Relay URL
    for t in tokens:
        clean_t = t.strip()
        if not clean_t:
            continue
        # 1. 检查是否为标准邮箱
        if not email and EMAIL_RE.match(clean_t):
            email = clean_t.lower()
            continue
        # 2. 检查是否为 Relay URL
        if not relay_url and (clean_t.lower().startswith(("http://", "https://")) or URL_RE.match(clean_t)):
            relay_url = clean_t
            continue
        # 3. 检查是否为 Client ID (标准 UUID 格式)
        if not client_id and UUID_RE.match(clean_t):
            client_id = clean_t
            continue

        remaining_tokens.append(clean_t)

    # 阶段 1.5: 若未能直接匹配完整邮箱，尝试从整行中提取邮箱正则
    if not email:
        for i, t in enumerate(remaining_tokens):
            m = EMAIL_EXTRACT_RE.search(t)
            if m:
                email = m.group(0).lower()
                remainder = t.replace(m.group(0), "").strip(" -_:,|\t")
                if remainder:
                    remaining_tokens[i] = remainder
                else:
                    remaining_tokens.pop(i)
                break

    if not email:
        return {
            "ok": False,
            "email": "",
            "password": "",
            "client_id": "",
            "refresh_token": "",
            "relay_url": "",
            "kind": default_kind,
            "raw_line": line,
            "detected_format": sep_name,
            "error": "未能在行内找到合法邮箱地址 (@domain)",
        }

    # 阶段 2: 对剩余 token 进一步按长度和特征归类 (Refresh Token / Client ID / Password)
    rt_candidates = []
    other_tokens = []

    for t in remaining_tokens:
        # Client ID 识别
        if not client_id and (len(t) in (32, 36) and not any(c in t for c in "!@#$%^&*()_=+")):
            if UUID_RE.match(t) or (len(t) == 32 and all(c in "0123456789abcdefABCDEF" for c in t)):
                client_id = t
                continue

        # 明显的 Refresh Token
        if len(t) >= 45 or t.startswith(("M.R3_", "M.C", "0.A", "r1.a", "AQABAAAA")):
            rt_candidates.append(t)
        else:
            other_tokens.append(t)

    # 分配 Refresh Token
    if rt_candidates:
        rt_candidates.sort(key=lambda x: len(x), reverse=True)
        refresh_token = rt_candidates[0]
        for extra in rt_candidates[1:]:
            other_tokens.append(extra)

    # 分配 Client ID (若仍为空且 other_tokens 中有候选)
    if not client_id and other_tokens:
        for i, t in enumerate(other_tokens):
            if UUID_RE.match(t) or (len(t) == 32 and all(c in "0123456789abcdefABCDEF" for c in t)):
                client_id = t
                other_tokens.pop(i)
                break

    # 分配 Password
    if other_tokens:
        password = other_tokens[0]
        if not client_id and len(other_tokens) >= 2:
            client_id = other_tokens[1]
        elif not refresh_token and len(other_tokens) >= 2:
            refresh_token = other_tokens[1]

    if not refresh_token and other_tokens and len(other_tokens) > 1:
        if other_tokens[1] != password:
            refresh_token = other_tokens[1]

    # 阶段 3: 确定邮箱类型与格式标签
    determined_kind = default_kind or "outlook"
    if relay_url:
        determined_kind = "icloud_relay"
    elif not determined_kind or determined_kind == "base":
        determined_kind = "outlook"

    parts_desc = ["邮箱"]
    if password:
        parts_desc.append("密码")
    if client_id:
        parts_desc.append("Client-ID")
    if refresh_token:
        parts_desc.append(f"RT({len(refresh_token)}位)")
    if relay_url:
        parts_desc.append("中转URL")

    format_summary = f"{sep_name} [{'+'.join(parts_desc)}]"

    return {
        "ok": True,
        "email": email,
        "password": password,
        "client_id": client_id,
        "refresh_token": refresh_token,
        "relay_url": relay_url,
        "kind": determined_kind,
        "raw_line": line,
        "detected_format": format_summary,
        "error": "",
    }
