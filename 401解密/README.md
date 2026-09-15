# 401 解密（独立包）

把主工程「解 401」拆出来的可运行包。逻辑与当前 WebUI 解 401 对齐：

- 代理按你填的原串走（`socks5://` 不会改成 `http://`，不换 sticky sid）
- 有可用 refresh_token 先极速刷 RT
- 勾选「允许重新登录」才走 **密码 + TOTP 2FA** 重登 Codex OAuth
- **不接码**。OpenAI 要绑手机就标记 `need_phone`
- 没填代理时并发强制为 1

适合：拷给别人、或嵌进对方系统。

---

## 环境

- Python 3.10+
- Node.js（Sentinel PoW 要求，需能运行 `node`）
- 依赖：`pip install -r requirements.txt`

---

## 直接使用（网页）

Windows 双击 `启动.bat`，或：

```bash
cd 401解密
pip install -r requirements.txt
python run.py
```

浏览器打开 `http://127.0.0.1:8891/`。

1. 填代理：`socks5://用户:密码@主机:端口` 或 `http://用户:密码@主机:端口`
2. 点「验证代理」，确认出口 IP
3. 粘贴账号，一行一个：

```
user@mail.com----Password123----JBSWY3DPEHPK3PXP
```

第四段可以带已有 RT（有 RT 会先刷，失败再按勾选决定是否重登）：

```
user@mail.com----Password123----JBSWY3DPEHPK3PXP----rt_xxxxx
```

4. 勾选「允许重新登录」（没有 RT 时必须勾）
5. 点「开始解密」
6. 成功后下载 CPA / Sub2，或看 `exports/cpa/`、`exports/sub2api/`

凭证会写到 `data/accounts.json`，下次同一邮箱会复用 `device_id` 和浏览器画像。

---

## 嵌进对方系统

把整个 `401解密` 文件夹放进项目，保证能 `import engine`（把该目录加入 `PYTHONPATH` 或 `sys.path`）。

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "401解密"))

from engine import parse_account_text, start, get_task, check_user_proxy, collect_exports
import time

check_user_proxy("socks5://user:pass@host:port")

accounts = parse_account_text("""
alice@x.com----Pass1----JBSWY3DPEHPK3PXP
bob@x.com----Pass2----MFRGGZDFMZTWQ2LK
""")

info = start(
    accounts,
    proxy_text="socks5://user:pass@host:port",
    workers=3,
    timeout=45,
    force_full_login=True,   # 没有 RT 必须 True
)
task_id = info["task_id"]

while True:
    snap = get_task(task_id).snapshot()
    print(snap["done_count"], "/", snap["total"], snap["stats"])
    if snap["finished_at"]:
        break
    time.sleep(1)

print(collect_exports(task_id, "cpa"))
```

也接受 JSON：

```python
start(
    [{
        "email": "alice@x.com",
        "password": "Pass1",
        "totp_secret": "JBSWY3DPEHPK3PXP",
        "refresh_token": "",          # 可选
        "device_id": "",              # 可选，没有会生成并钉死
        "browser_profile": {},        # 可选，有注册画像请原样传入
        "reg_country": "US",
    }],
    proxy_text="http://user:pass@host:port",
    force_full_login=True,
)
```

HTTP 接口（本包 FastAPI）：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/proxy/check` | `{"proxy":"..."}` 测出口 |
| POST | `/api/start` | `accounts/proxy/workers/timeout/force_full_login` |
| GET | `/api/snapshot/{task_id}` | 进度 |
| GET | `/api/stream/{task_id}` | SSE |
| GET | `/api/log/{task_id}/{email}` | 单号日志 |
| POST | `/api/stop/{task_id}` | 停止 |
| GET | `/api/download/cpa/{task_id}` | 成功 CPA JSON |
| GET | `/api/download/sub2/{task_id}` | 成功 Sub2 JSON |

---

## 行为说明（务必读）

1. **代理**：写什么协议就走什么协议。一条代理时所有并发共用这一条，不换 sid。
2. **RT**：长度 &lt; 20 或值为 `1` 的假 RT 会忽略。
3. **重登**：需要密码 + TOTP。没有注册时的 `browser_profile` / `device_id` 时会生成一套并写入 `data/accounts.json`。这会被官方当成新设备，封号风险高于「复用注册画像」。有原画像请用 JSON 带上。
4. **不接码**：返回 `need_phone` 表示官方要绑手机，本包不会去租号。
5. **并发**：界面 1～5；没填代理强制 1。
6. **导出**：CPA 单文件给 CPAMC；Sub2 给 Sub2API。`refresh_token` 为空时 CPA 里会写成 `"1"`（面板占位），以实际刷到的 RT 为准。

---

## 目录

```
401解密/
  run.py                 网页服务入口
  engine.py              解 401 引擎（集成主要改这里）
  启动.bat
  requirements.txt
  static/index.html
  auth_flow.py / fingerprint.py / http_client.py / sentinel*.py
  mail_providers/        主工程邮箱库（重登兜底会用到）
  webui/oauth_export.py  Codex OAuth（skip_sms + pin_proxy）
  data/accounts.json     运行后生成
  exports/cpa|sub2api    成功导出
```

协议实现来自主工程当前解 401，不要改 `pin_proxy=True` 和 `skip_sms=True`。
