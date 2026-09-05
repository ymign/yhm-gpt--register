"""浏览器指纹（默认对齐 yhm-gpt-free-register 的 HAR / Roxy 桌面 Chrome 画像）。

每次注册调用 generate_fingerprint() 生成一套一致的指纹组合：
  - TLS impersonate（curl_cffi 最高 chrome146）
  - HTTP/JS 自称 Chrome/149 macOS（与 2026-07-19 ChatGPT 抓包一致）
  - sec-ch-ua 全套 Client Hints（仅 Chromium）
  - 屏幕 / 硬件 / WebGL
  - Accept-Language + IANA 时区（跟出口国家，不再随机混语言）
  - browser_type / browser_os
  - fallback_impersonates 同家族 TLS 回退列表

Safari / iOS / Firefox 生成器仍保留，供 TLS 旋转查表；默认注册不再抽它们。
ChatGPT Plus 试用资格是注册后第一次 chatgpt.com 会话的 A/B 曝光，
桌面 Chrome 149 + 语言/时区跟出口 IP 一致，才接近指纹浏览器的命中率。
"""
from __future__ import annotations

import random
import uuid

# ---------------------------------------------------------------------------
# macOS Safari（保留原有）
# ---------------------------------------------------------------------------
_SAFARI_VERSIONS = [
    {
        "impersonate": "safari15_3",
        "safari_ver": "15.3",
        "webkit_ver": "605.1.15",
        "macos_versions": ["10_15_7", "12_0", "12_1"],
    },
    {
        "impersonate": "safari15_5",
        "safari_ver": "15.5",
        "webkit_ver": "605.1.15",
        "macos_versions": ["10_15_7", "12_4", "12_5"],
    },
    {
        "impersonate": "safari17_0",
        "safari_ver": "17.0",
        "webkit_ver": "605.1.15",
        "macos_versions": ["13_6", "14_0", "14_1"],
    },
    {
        "impersonate": "safari18_0",
        "safari_ver": "18.0",
        "webkit_ver": "605.1.15",
        "macos_versions": ["14_4", "14_5", "15_0", "15_1"],
    },
]

_MAC_SCREENS = [
    "1440x900",
    "1512x982",
    "1728x1117",
    "2560x1440",
    "1920x1080",
]

# ---------------------------------------------------------------------------
# iOS Safari
# ---------------------------------------------------------------------------
_IOS_SAFARI_VERSIONS = [
    {
        "impersonate": "safari17_2_ios",
        "safari_ver": "17.2",
        "webkit_ver": "605.1.15",
        "ios_versions": ["17_1_2", "17_2"],
    },
    {
        "impersonate": "safari18_0_ios",
        "safari_ver": "18.0",
        "webkit_ver": "605.1.15",
        "ios_versions": ["18_0", "18_1", "18_1_1"],
    },
]

_IPHONE_SCREENS = [
    "390x844",   # iPhone 13 / 14
    "393x852",   # iPhone 14 Pro / 15
    "428x926",   # iPhone 13 Pro Max / 14 Plus
    "430x932",   # iPhone 14 Pro Max / 15 Plus
]

# ---------------------------------------------------------------------------
# Chrome（HTTP/JS = 149，TLS = curl_cffi chrome146）
#
# yhm-gpt-free-register 的 HAR / 协议画像：
#   UA Chrome/149.0.0.0 + Macintosh; Intel Mac OS X 10_15_7（Chrome 冻结 UA）
#   sec-ch-ua: "Google Chrome";v="149", "Chromium";v="149", "Not)A;Brand";v="24"
#   TLS impersonate 仍用 chrome146（curl_cffi 0.15 最高内置版本）
# ---------------------------------------------------------------------------
_CHROME_HAR = {
    "impersonate": "chrome146",
    "ver": "149",
    "full_ver": "149.0.0.0",
    "not_a_brand": '"Not)A;Brand";v="24"',
    "not_a_brand_full": '"Not)A;Brand";v="24.0.0.0"',
}

# TLS 旋转查表用；generate_fingerprint 默认只用 _CHROME_HAR。
_CHROME_VERSIONS = [
    _CHROME_HAR,
    {
        "impersonate": "chrome142",
        "ver": "142",
        "full_ver": "142.0.0.0",
        "not_a_brand": '"Not/A)Brand";v="8"',
        "not_a_brand_full": '"Not/A)Brand";v="8.0.0.0"',
    },
    {
        "impersonate": "chrome136",
        "ver": "136",
        "full_ver": "136.0.0.0",
        "not_a_brand": '"Not.A/Brand";v="99"',
        "not_a_brand_full": '"Not.A/Brand";v="99.0.0.0"',
    },
]

# 同一账号会话内 TLS 不换版本：换 HTTP 主版本会变成「一号两套环境」。
_CHROME_TLS_FALLBACKS = ["chrome146"]

_MAC_OS_UA_VERSION = "10_15_7"  # Chrome 桌面冻结 UA，与真实 Chrome 149 一致
_MAC_CHROME_PLATFORM_VERSION = "15.7.0"
_MAC_CHROME_ARCH = "arm"
# 每号抽一套，会话内锁定。UA 仍是冻结的 10_15_7，和真 Chrome 一致。
_MAC_CHROME_PLATFORM_VERSIONS = ["14.7.1", "15.3.1", "15.6.1", "15.7.0"]
_MAC_CHROME_ARCHS = ["arm", "arm", "arm", "x86"]

_MAC_CHROME_SCREENS = [
    "1440x900",
    "1512x982",
    "1680x1050",
    "1728x1117",
    "1800x1169",
    "2056x1329",
]

_WIN_SCREENS = [
    "1920x1080",
    "1366x768",
    "2560x1440",
    "1536x864",
    "1440x900",
]

# ---------------------------------------------------------------------------
# Firefox (Windows)
# ---------------------------------------------------------------------------
_FIREFOX_VERSIONS = [
    {"impersonate": "firefox133", "ver": "133.0"},
    {"impersonate": "firefox144", "ver": "144.0"},
]

# ---------------------------------------------------------------------------
# 国家 → 时区/语言画像（IP 地理联动优化）
# ---------------------------------------------------------------------------
_COUNTRY_PROFILES = {
    # 亚洲
    "JP": {
        "timezones": [("Asia/Tokyo", 1.0)],
        "languages": ["ja-JP", "ja", "en-US", "en", "zh-CN"],
    },
    "CN": {
        "timezones": [("Asia/Shanghai", 1.0)],
        "languages": ["zh-CN", "zh", "en-US", "en"],
    },
    "HK": {
        "timezones": [("Asia/Hong_Kong", 1.0)],
        "languages": ["zh-HK", "zh-CN", "zh", "en-US", "en"],
    },
    "TW": {
        "timezones": [("Asia/Taipei", 1.0)],
        "languages": ["zh-TW", "zh", "en-US", "en", "ja"],
    },
    "KR": {
        "timezones": [("Asia/Seoul", 1.0)],
        "languages": ["ko-KR", "ko", "en-US", "en", "ja"],
    },
    "SG": {
        "timezones": [("Asia/Singapore", 1.0)],
        "languages": ["en-SG", "en-US", "en", "zh-CN", "zh"],
    },
    "MY": {
        "timezones": [("Asia/Kuala_Lumpur", 1.0)],
        "languages": ["ms-MY", "ms", "zh-CN", "zh", "en-US", "en"],
    },
    "TH": {
        "timezones": [("Asia/Bangkok", 1.0)],
        "languages": ["th-TH", "th", "en-US", "en"],
    },
    "VN": {
        "timezones": [("Asia/Ho_Chi_Minh", 1.0)],
        "languages": ["vi-VN", "vi", "en-US", "en"],
    },
    "IN": {
        "timezones": [("Asia/Kolkata", 1.0)],
        "languages": ["en-IN", "en-US", "en", "hi-IN", "hi"],
    },
    "ID": {
        "timezones": [("Asia/Jakarta", 1.0)],
        "languages": ["id-ID", "id", "en-US", "en"],
    },
    "PH": {
        "timezones": [("Asia/Manila", 1.0)],
        "languages": ["en-US", "en", "tl-PH", "tl"],
    },
    "PK": {
        "timezones": [("Asia/Karachi", 1.0)],
        "languages": ["en-US", "en", "ur-PK", "ur"],
    },
    "BD": {
        "timezones": [("Asia/Dhaka", 1.0)],
        "languages": ["bn-BD", "bn", "en-US", "en"],
    },
    "IL": {
        "timezones": [("Asia/Jerusalem", 1.0)],
        "languages": ["he-IL", "he", "en-US", "en", "ar"],
    },
    "TR": {
        "timezones": [("Europe/Istanbul", 1.0)],
        "languages": ["tr-TR", "tr", "en-US", "en"],
    },
    "SA": {
        "timezones": [("Asia/Riyadh", 1.0)],
        "languages": ["ar-SA", "ar", "en-US", "en"],
    },
    "AE": {
        "timezones": [("Asia/Dubai", 1.0)],
        "languages": ["ar-AE", "ar", "en-US", "en"],
    },
    # 北美
    "US": {
        "timezones": [
            ("America/New_York", 0.4),      # 东部（数据中心多）
            ("America/Los_Angeles", 0.3),   # 西部
            ("America/Chicago", 0.2),       # 中部
            ("America/Denver", 0.1),        # 山地
        ],
        "languages": ["en-US", "en", "es-US", "es", "zh-CN"],
    },
    "CA": {
        "timezones": [
            ("America/Toronto", 0.6),       # 东部（安大略）
            ("America/Vancouver", 0.3),     # 西部（BC）
            ("America/Edmonton", 0.1),      # 山地（阿尔伯塔）
        ],
        "languages": ["en-CA", "en-US", "en", "fr-CA", "fr"],
    },
    "MX": {
        "timezones": [("America/Mexico_City", 1.0)],
        "languages": ["es-MX", "es", "en-US", "en"],
    },
    # 南美
    "BR": {
        "timezones": [
            ("America/Sao_Paulo", 0.7),
            ("America/Manaus", 0.2),
            ("America/Fortaleza", 0.1),
        ],
        "languages": ["pt-BR", "pt", "en-US", "en", "es"],
    },
    "AR": {
        "timezones": [("America/Argentina/Buenos_Aires", 1.0)],
        "languages": ["es-AR", "es", "en-US", "en"],
    },
    "CL": {
        "timezones": [("America/Santiago", 1.0)],
        "languages": ["es-CL", "es", "en-US", "en"],
    },
    "CO": {
        "timezones": [("America/Bogota", 1.0)],
        "languages": ["es-CO", "es", "en-US", "en"],
    },
    # 欧洲
    "GB": {
        "timezones": [("Europe/London", 1.0)],
        "languages": ["en-GB", "en-US", "en", "fr", "de"],
    },
    "DE": {
        "timezones": [("Europe/Berlin", 1.0)],
        "languages": ["de-DE", "de", "en-US", "en", "fr"],
    },
    "FR": {
        "timezones": [("Europe/Paris", 1.0)],
        "languages": ["fr-FR", "fr", "en-US", "en", "de"],
    },
    "IT": {
        "timezones": [("Europe/Rome", 1.0)],
        "languages": ["it-IT", "it", "en-US", "en", "fr"],
    },
    "ES": {
        "timezones": [("Europe/Madrid", 1.0)],
        "languages": ["es-ES", "es", "en-US", "en", "fr"],
    },
    "NL": {
        "timezones": [("Europe/Amsterdam", 1.0)],
        "languages": ["nl-NL", "nl", "en-US", "en", "de"],
    },
    "BE": {
        "timezones": [("Europe/Brussels", 1.0)],
        "languages": ["nl-BE", "fr-BE", "nl", "fr", "en-US", "en"],
    },
    "CH": {
        "timezones": [("Europe/Zurich", 1.0)],
        "languages": ["de-CH", "fr-CH", "de", "fr", "it", "en-US", "en"],
    },
    "SE": {
        "timezones": [("Europe/Stockholm", 1.0)],
        "languages": ["sv-SE", "sv", "en-US", "en"],
    },
    "NO": {
        "timezones": [("Europe/Oslo", 1.0)],
        "languages": ["nb-NO", "nb", "en-US", "en"],
    },
    "DK": {
        "timezones": [("Europe/Copenhagen", 1.0)],
        "languages": ["da-DK", "da", "en-US", "en"],
    },
    "FI": {
        "timezones": [("Europe/Helsinki", 1.0)],
        "languages": ["fi-FI", "fi", "sv", "en-US", "en"],
    },
    "PL": {
        "timezones": [("Europe/Warsaw", 1.0)],
        "languages": ["pl-PL", "pl", "en-US", "en"],
    },
    "RU": {
        "timezones": [
            ("Europe/Moscow", 0.7),         # 莫斯科（MSK，主要数据中心）
            ("Asia/Yekaterinburg", 0.15),   # 叶卡捷琳堡（+5）
            ("Asia/Novosibirsk", 0.15),     # 新西伯利亚（+7）
        ],
        "languages": ["ru-RU", "ru", "en-US", "en"],
    },
    "UA": {
        "timezones": [("Europe/Kiev", 1.0)],
        "languages": ["uk-UA", "uk", "ru", "en-US", "en"],
    },
    "CZ": {
        "timezones": [("Europe/Prague", 1.0)],
        "languages": ["cs-CZ", "cs", "en-US", "en", "de"],
    },
    "AT": {
        "timezones": [("Europe/Vienna", 1.0)],
        "languages": ["de-AT", "de", "en-US", "en"],
    },
    "GR": {
        "timezones": [("Europe/Athens", 1.0)],
        "languages": ["el-GR", "el", "en-US", "en"],
    },
    "PT": {
        "timezones": [("Europe/Lisbon", 1.0)],
        "languages": ["pt-PT", "pt", "en-US", "en", "es"],
    },
    # 大洋洲
    "AU": {
        "timezones": [
            ("Australia/Sydney", 0.5),      # 悉尼（NSW，数据中心多）
            ("Australia/Melbourne", 0.3),   # 墨尔本（VIC）
            ("Australia/Brisbane", 0.2),    # 布里斯班（QLD）
        ],
        "languages": ["en-AU", "en-US", "en", "zh-CN", "zh"],
    },
    "NZ": {
        "timezones": [("Pacific/Auckland", 1.0)],
        "languages": ["en-NZ", "en-US", "en"],
    },
    # 非洲
    "ZA": {
        "timezones": [("Africa/Johannesburg", 1.0)],
        "languages": ["en-ZA", "en-US", "en", "af"],
    },
    "EG": {
        "timezones": [("Africa/Cairo", 1.0)],
        "languages": ["ar-EG", "ar", "en-US", "en"],
    },
    "NG": {
        "timezones": [("Africa/Lagos", 1.0)],
        "languages": ["en-NG", "en-US", "en"],
    },
    "KE": {
        "timezones": [("Africa/Nairobi", 1.0)],
        "languages": ["sw-KE", "sw", "en-US", "en"],
    },
}

# 兜底策略（未知国家）
_DEFAULT_COUNTRY_PROFILE = {
    "timezones": [("UTC", 1.0)],
    "languages": ["en-US", "en"],
}

# ---------------------------------------------------------------------------
# 共享（旧的固定语言列表，保留兼容性）
# ---------------------------------------------------------------------------
_LANGUAGES = [
    ("en-US", "en-US,en;q=0.9"),
    ("en-US", "en-US,en;q=0.9,zh-CN;q=0.8,zh;q=0.7"),
    ("en-GB", "en-GB,en;q=0.9,en-US;q=0.8"),
    ("en-US", "en-US,en;q=0.9,ja;q=0.8"),
]

# 默认只出桌面 Chrome。Safari/iOS/Firefox 的 TLS 与 JS 画像对不上 ChatGPT Web
# 的 A/B 分流（Plus 试用几乎只给桌面 Chrome 曝光），旧权重会把命中率打下去。
_BROWSER_WEIGHTS = [
    ("chrome",     100),
    ("mac_safari", 0),
    ("ios_safari", 0),
    ("firefox",    0),
]

_BROWSER_TYPES = [t for t, _ in _BROWSER_WEIGHTS]
_WEIGHTS = [w for _, w in _BROWSER_WEIGHTS]


# ---------------------------------------------------------------------------
# 硬件 / navigator 一致性画像（按浏览器家族绑定）
#
# 关键点：navigator.platform / vendor / deviceMemory 在不同引擎行为不同——
#   - vendor:       Safari/iOS="Apple Computer, Inc."，Chrome="Google Inc."，
#                   Firefox=""（空串，不是 undefined）
#   - deviceMemory: 仅 Chromium 暴露且 spec 封顶 8；Safari/Firefox 为 None(undefined)
#   - platform:     mac_safari=MacIntel, ios_safari=iPhone, chrome/firefox=Win32
#   - maxTouchPoints: 只有 iOS 触摸屏=5，其余=0
#   - devicePixelRatio: Retina=2.0/3.0，Windows 常见 1.0/1.25/1.5
# 这些值在一次注册内必须**固定**（真实浏览器同会话不会变），故在
# generate_fingerprint() 里用同一个 RNG 一次性定死，写进指纹 dict。
# ---------------------------------------------------------------------------
_HARDWARE_PROFILES = {
    "mac_safari": {
        "navigator_platform": "MacIntel",
        "navigator_vendor": "Apple Computer, Inc.",
        "hardware_concurrency": [8, 10, 12, 16],
        "device_memory": [None],          # Safari 不暴露 deviceMemory
        "max_touch_points": [0],
        "device_pixel_ratio": [2.0],      # Retina 必定 2.0
    },
    "ios_safari": {
        "navigator_platform": "iPhone",
        "navigator_vendor": "Apple Computer, Inc.",
        "hardware_concurrency": [4, 6],   # A15/A16/A17
        "device_memory": [None],          # iOS Safari 不暴露
        "max_touch_points": [5],          # 触摸屏
        "device_pixel_ratio": [2.0, 3.0],
    },
    "chrome": {
        "navigator_platform": "Win32",
        "navigator_vendor": "Google Inc.",
        "hardware_concurrency": [4, 6, 8, 12, 16, 24],
        "device_memory": [4, 8],          # spec 封顶 8
        "max_touch_points": [0],
        "device_pixel_ratio": [1.0, 1.25, 1.5],
    },
    "chrome_mac": {
        "navigator_platform": "MacIntel",
        "navigator_vendor": "Google Inc.",
        "hardware_concurrency": [6, 8, 10, 12],
        "device_memory": [8],
        "max_touch_points": [0],
        "device_pixel_ratio": [2.0],
    },
    "firefox": {
        "navigator_platform": "Win32",
        "navigator_vendor": "",           # Firefox navigator.vendor 为空串
        "hardware_concurrency": [4, 6, 8, 12, 16],
        "device_memory": [None],          # Firefox 不暴露 deviceMemory
        "max_touch_points": [0],
        "device_pixel_ratio": [1.0, 1.5],
    },
}


_WEBGL_PROFILES = {
    "mac_safari": [
        ("Apple", "Apple M1"),
        ("Apple", "Apple M2"),
        ("Apple", "Apple M3 Pro"),
        ("Apple", "Apple M3 Max"),
    ],
    "ios_safari": [
        ("Apple Inc.", "Apple GPU"),
    ],
    "chrome": [
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4070 Laptop GPU Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon(TM) Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ],
    "chrome_mac": [
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M1 Pro, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)"),
        ("Google Inc. (Apple)", "ANGLE (Apple, ANGLE Metal Renderer: Apple M3 Pro, Unspecified Version)"),
    ],
    "firefox": [
        ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3070 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
        ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
    ],
}


def _hardware_profile_key(fp: dict) -> str:
    if fp.get("browser_type") == "chrome" and fp.get("browser_os") == "macOS":
        return "chrome_mac"
    return fp.get("browser_type") or "chrome"


def _apply_hardware(fp: dict, r: random.Random) -> None:
    """按 browser_type / browser_os 从画像池抽一套一致的硬件参数写进指纹 dict。"""
    key = _hardware_profile_key(fp)
    prof = _HARDWARE_PROFILES.get(key, _HARDWARE_PROFILES["chrome"])
    fp["navigator_platform"] = prof["navigator_platform"]
    fp["navigator_vendor"] = prof["navigator_vendor"]
    fp["hardware_concurrency"] = r.choice(prof["hardware_concurrency"])
    fp["device_memory"] = r.choice(prof["device_memory"])
    fp["max_touch_points"] = r.choice(prof["max_touch_points"])
    fp["device_pixel_ratio"] = r.choice(prof["device_pixel_ratio"])

    # WebGL / Audio 硬件指纹一致性绑定
    w_vendor, w_renderer = r.choice(_WEBGL_PROFILES.get(key, _WEBGL_PROFILES["chrome"]))
    fp["webgl_vendor"] = w_vendor
    fp["webgl_renderer"] = w_renderer
    fp["audio_sample_rate"] = r.choice([44100, 48000])
    fp["color_depth"] = 24
    if key == "chrome_mac":
        fp["js_heap_size_limit"] = r.choice([4294967296, 4395630592])
    elif fp.get("browser_type") == "chrome":
        fp["js_heap_size_limit"] = 4294967296


# ---------------------------------------------------------------------------
# 指纹生成
# ---------------------------------------------------------------------------

def _gen_mac_safari(r: random.Random) -> dict:
    safari = r.choice(_SAFARI_VERSIONS)
    macos_ver = r.choice(safari["macos_versions"])
    others = [s["impersonate"] for s in _SAFARI_VERSIONS if s["impersonate"] != safari["impersonate"]]
    return {
        "browser_type": "mac_safari",
        "browser_os": "macOS",
        "impersonate": safari["impersonate"],
        "fallback_impersonates": [safari["impersonate"]] + r.sample(others, min(2, len(others))),
        "user_agent": (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {macos_ver}) "
            f"AppleWebKit/{safari['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{safari['safari_ver']} Safari/{safari['webkit_ver']}"
        ),
        "sec_ch_ua": "",
        "sec_ch_ua_platform": "",
        "sec_ch_ua_mobile": "",
        "screen": r.choice(_MAC_SCREENS),
    }


def _gen_ios_safari(r: random.Random) -> dict:
    safari = r.choice(_IOS_SAFARI_VERSIONS)
    ios_ver = r.choice(safari["ios_versions"])
    others = [s["impersonate"] for s in _IOS_SAFARI_VERSIONS if s["impersonate"] != safari["impersonate"]]
    fallbacks = [safari["impersonate"]] + others
    return {
        "browser_type": "ios_safari",
        "browser_os": "iOS",
        "impersonate": safari["impersonate"],
        "fallback_impersonates": fallbacks,
        "user_agent": (
            f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_ver} like Mac OS X) "
            f"AppleWebKit/{safari['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{safari['safari_ver']} Mobile/15E148 Safari/604.1"
        ),
        "sec_ch_ua": "",
        "sec_ch_ua_platform": "",
        "sec_ch_ua_mobile": "",
        "screen": r.choice(_IPHONE_SCREENS),
    }


def _chrome_client_hints(chrome: dict, *, platform: str, platform_version: str, arch: str) -> dict:
    """Chrome Client Hints：品牌顺序与 HAR 一致（Google Chrome → Chromium → Not)A;Brand）。"""
    not_a_full = chrome.get("not_a_brand_full") or chrome["not_a_brand"]
    return {
        "sec_ch_ua": (
            f'"Google Chrome";v="{chrome["ver"]}", '
            f'"Chromium";v="{chrome["ver"]}", '
            f'{chrome["not_a_brand"]}'
        ),
        "sec_ch_ua_full_version_list": (
            f'"Google Chrome";v="{chrome["full_ver"]}", '
            f'"Chromium";v="{chrome["full_ver"]}", '
            f'{not_a_full}'
        ),
        "sec_ch_ua_platform": f'"{platform}"',
        "sec_ch_ua_mobile": "?0",
        "sec_ch_ua_arch": f'"{arch}"',
        "sec_ch_ua_bitness": '"64"',
        "sec_ch_ua_model": '""',
        "sec_ch_ua_platform_version": f'"{platform_version}"',
    }


def _chrome_user_agent(full_ver: str, browser_os: str) -> str:
    if browser_os == "macOS":
        return (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {_MAC_OS_UA_VERSION}) "
            f"AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{full_ver} Safari/537.36"
        )
    return (
        f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{full_ver} Safari/537.36"
    )


def _gen_chrome(r: random.Random) -> dict:
    chrome = _CHROME_HAR
    fallbacks = list(_CHROME_TLS_FALLBACKS)
    platform_version = r.choice(_MAC_CHROME_PLATFORM_VERSIONS)
    arch = r.choice(_MAC_CHROME_ARCHS)
    hints = _chrome_client_hints(
        chrome,
        platform="macOS",
        platform_version=platform_version,
        arch=arch,
    )
    return {
        "browser_type": "chrome",
        "browser_os": "macOS",
        "env_id": uuid.uuid4().hex,
        "impersonate": chrome["impersonate"],
        "fallback_impersonates": fallbacks,
        "user_agent": _chrome_user_agent(chrome["full_ver"], "macOS"),
        **hints,
        "screen": r.choice(_MAC_CHROME_SCREENS),
    }


def _gen_firefox(r: random.Random) -> dict:
    ff = r.choice(_FIREFOX_VERSIONS)
    others = [f["impersonate"] for f in _FIREFOX_VERSIONS if f["impersonate"] != ff["impersonate"]]
    fallbacks = [ff["impersonate"]] + others
    return {
        "browser_type": "firefox",
        "browser_os": "Windows",
        "impersonate": ff["impersonate"],
        "fallback_impersonates": fallbacks,
        "user_agent": (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{ff['ver']}) "
            f"Gecko/20100101 Firefox/{ff['ver']}"
        ),
        "sec_ch_ua": "",
        "sec_ch_ua_platform": "",
        "sec_ch_ua_mobile": "",
        "screen": r.choice(_WIN_SCREENS),
    }


_GENERATORS = {
    "mac_safari": _gen_mac_safari,
    "ios_safari": _gen_ios_safari,
    "chrome": _gen_chrome,
    "firefox": _gen_firefox,
}


def _primary_lang_from_profile(profile: dict) -> str:
    langs = list(profile.get("languages") or ["en-US", "en"])
    for lang in langs:
        if lang and "-" in lang:
            return lang
    return langs[0] if langs else "en-US"


def _build_accept_language(primary: str) -> str:
    """真实 Chrome 的 Accept-Language：主语言 + 短码 + en，不再随机拼 5 种无关语言。"""
    primary = (primary or "en-US").strip() or "en-US"
    lower = primary.lower()
    if lower == "en-us":
        return "en-US,en;q=0.9"
    if lower == "en":
        return "en,en-US;q=0.9"
    short = primary.split("-", 1)[0]
    if lower.startswith("en-"):
        return f"{primary},en;q=0.9,en-US;q=0.8"
    return f"{primary},{short};q=0.9,en-US;q=0.8,en;q=0.7"


def _locale_from_country(country_code: str, r: random.Random) -> tuple[str, str, str]:
    """返回 (lang, lang_full, timezone)，时区按国家加权，语言跟真实 Chrome。"""
    profile = _COUNTRY_PROFILES.get((country_code or "").strip().upper(), _DEFAULT_COUNTRY_PROFILE)
    tz_choices = profile["timezones"]
    tz_list = [tz for tz, _ in tz_choices]
    tz_weights = [w for _, w in tz_choices]
    timezone = r.choices(tz_list, weights=tz_weights, k=1)[0]
    primary = _primary_lang_from_profile(profile)
    return primary, _build_accept_language(primary), timezone


def apply_geo_to_fingerprint(fp: dict, country_code: str, rng: random.Random | None = None) -> dict:
    """只改语言/时区，不换浏览器家族。探测到出口国家后应走这里，而不是重新 generate。"""
    out = dict(fp or {})
    lang, lang_full, timezone = _locale_from_country(country_code, rng or random)
    out["lang"] = lang
    out["lang_full"] = lang_full
    out["timezone"] = timezone
    out["geo_country"] = (country_code or "").strip().upper()
    return out


def generate_fingerprint(rng: random.Random | None = None, country_code: str = "") -> dict:
    """生成一套一致的浏览器指纹。

    参数:
        rng: 随机数生成器（传入可保证会话内一致性）
        country_code: IP 地理国家码（如 JP/US/DE），用于时区/语言联动优化

    返回 dict:
        browser_type: str     — 浏览器家族（默认 chrome）
        browser_os: str       — macOS / Windows / iOS
        impersonate: str      — curl_cffi TLS 指纹名
        fallback_impersonates: list[str] — 同家族回退 impersonate 列表
        user_agent: str       — 完整 UA 字符串
        sec_ch_ua: str        — Client Hints（仅 Chrome 非空）
        sec_ch_ua_platform: str
        sec_ch_ua_mobile: str
        sec_ch_ua_full_version_list: str — 完整版本号列表（仅 Chrome）
        sec_ch_ua_arch: str              — CPU 架构（仅 Chrome）
        sec_ch_ua_bitness: str           — 位数（仅 Chrome）
        sec_ch_ua_model: str             — 设备型号（仅 Chrome，桌面为空串）
        sec_ch_ua_platform_version: str  — OS 版本号（仅 Chrome）
        screen: str           — 屏幕分辨率 (WxH)
        lang: str             — 主语言
        lang_full: str        — 完整 Accept-Language
        timezone: str         — IANA 时区名（如 Asia/Tokyo）
        navigator_platform: str  — navigator.platform（MacIntel/iPhone/Win32）
        navigator_vendor: str    — navigator.vendor（按引擎；Firefox 为空串）
        hardware_concurrency: int — CPU 逻辑核心数
        device_memory: int|None   — navigator.deviceMemory（仅 Chromium 有值）
        max_touch_points: int     — navigator.maxTouchPoints（iOS=5）
        device_pixel_ratio: float — window.devicePixelRatio
    """
    r = rng or random
    # 注册默认锁定桌面 Chrome 149 / macOS。旧的 Safari/Firefox 权重会让 Plus 试用曝光几乎打空。
    browser_type = "chrome"
    fp = _GENERATORS[browser_type](r)
    fp.setdefault("browser_os", "macOS")

    country_code = (country_code or "").strip().upper()
    lang, lang_full, timezone = _locale_from_country(country_code, r)
    fp["lang"] = lang
    fp["lang_full"] = lang_full
    fp["timezone"] = timezone
    fp["geo_country"] = country_code
    _apply_hardware(fp, r)

    # 非 Chrome 家族补齐空值键（保证调用方统一取值不报 KeyError）
    if browser_type != "chrome":
        fp.setdefault("sec_ch_ua_full_version_list", "")
        fp.setdefault("sec_ch_ua_arch", "")
        fp.setdefault("sec_ch_ua_bitness", "")
        fp.setdefault("sec_ch_ua_model", "")
        fp.setdefault("sec_ch_ua_platform_version", "")

    return fp


# ---------------------------------------------------------------------------
# impersonate → UA 映射（TLS 旋转用）
# ---------------------------------------------------------------------------

_ALL_IMPERSONATES: dict[str, dict] = {}

for s in _SAFARI_VERSIONS:
    _ALL_IMPERSONATES[s["impersonate"]] = {"type": "mac_safari", "data": s}
for s in _IOS_SAFARI_VERSIONS:
    _ALL_IMPERSONATES[s["impersonate"]] = {"type": "ios_safari", "data": s}
for c in _CHROME_VERSIONS:
    _ALL_IMPERSONATES[c["impersonate"]] = {"type": "chrome", "data": c}
for f in _FIREFOX_VERSIONS:
    _ALL_IMPERSONATES[f["impersonate"]] = {"type": "firefox", "data": f}


def fingerprint_for_impersonate(impersonate: str, current_fp: dict) -> dict:
    """把指纹里**随 impersonate 版本变化**的字段同步到新版本，其余原样保留。

    TLS 旋转（_rotate_impersonate_session）换 impersonate 时，光换 UA 是不够的：
    _common_headers / _navigation_headers 的 sec-ch-ua* 全从指纹取，不同步就会出现
    「UA 说 Chrome/136、sec-ch-ua 说 v=146」，连 not_a_brand 都对不上
    （136:"Not.A/Brand";v="99" / 142:"Not/A)Brand";v="8" / 146:"Not?A_Brand";v="99"），
    是 CF 一抓一个准的自相矛盾特征。

    只动版本相关字段（sec_ch_ua / full_version_list / user_agent），
    屏幕、语言、时区、硬件等会话级属性保持不变 —— 那些跟浏览器版本无关，
    换了反而破坏"同一台机器"的一致性。

    未知 impersonate 或非 Chrome 家族：Safari/Firefox 本就不发 client hints
    （sec_ch_ua 为空串），无需同步，原样返回副本。
    """
    entry = _ALL_IMPERSONATES.get(impersonate)
    fp = dict(current_fp or {})
    if not entry:
        return fp

    t, d = entry["type"], entry["data"]
    fp["impersonate"] = impersonate
    fp["browser_type"] = t
    fp["user_agent"] = ua_for_impersonate(impersonate, fp.get("user_agent", ""))

    if t == "chrome":
        is_mac = (
            fp.get("browser_os") == "macOS"
            or "Macintosh" in str(fp.get("user_agent") or "")
            or str(fp.get("sec_ch_ua_platform") or "") == '"macOS"'
        )
        if is_mac:
            hints = _chrome_client_hints(
                d,
                platform="macOS",
                platform_version=(fp.get("sec_ch_ua_platform_version") or f'"{_MAC_CHROME_PLATFORM_VERSION}"').strip('"'),
                arch=(fp.get("sec_ch_ua_arch") or f'"{_MAC_CHROME_ARCH}"').strip('"'),
            )
            fp["browser_os"] = "macOS"
        else:
            platform_version = (fp.get("sec_ch_ua_platform_version") or '"10.0.19045"').strip('"')
            arch = (fp.get("sec_ch_ua_arch") or '"x86"').strip('"')
            hints = _chrome_client_hints(d, platform="Windows", platform_version=platform_version, arch=arch)
            fp["browser_os"] = "Windows"
        fp.update(hints)
    else:
        # 非 Chromium：一个 client hint 都不发（真实浏览器行为）
        fp["sec_ch_ua"] = ""
        fp["sec_ch_ua_platform"] = ""
        fp["sec_ch_ua_mobile"] = ""
        fp["sec_ch_ua_full_version_list"] = ""
        fp["sec_ch_ua_arch"] = ""
        fp["sec_ch_ua_bitness"] = ""
        fp["sec_ch_ua_model"] = ""
        fp["sec_ch_ua_platform_version"] = ""
    return fp


def ua_for_impersonate(impersonate: str, current_ua: str) -> str:
    """根据 impersonate 名生成匹配的 UA。"""
    entry = _ALL_IMPERSONATES.get(impersonate)
    if not entry:
        return current_ua

    t, d = entry["type"], entry["data"]

    if t == "mac_safari":
        macos_ver = random.choice(d["macos_versions"])
        return (
            f"Mozilla/5.0 (Macintosh; Intel Mac OS X {macos_ver}) "
            f"AppleWebKit/{d['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{d['safari_ver']} Safari/{d['webkit_ver']}"
        )
    elif t == "ios_safari":
        ios_ver = random.choice(d["ios_versions"])
        return (
            f"Mozilla/5.0 (iPhone; CPU iPhone OS {ios_ver} like Mac OS X) "
            f"AppleWebKit/{d['webkit_ver']} (KHTML, like Gecko) "
            f"Version/{d['safari_ver']} Mobile/15E148 Safari/604.1"
        )
    elif t == "chrome":
        browser_os = "macOS" if "Macintosh" in (current_ua or "") else "Windows"
        return _chrome_user_agent(d["full_ver"], browser_os)
    elif t == "firefox":
        return (
            f"Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:{d['ver']}) "
            f"Gecko/20100101 Firefox/{d['ver']}"
        )
    return current_ua
