# ⚡ GPT 账号密码 2FA 管理与 Sub2API 转换助手 (SQLite 持久化版)

> **独立单机版工具**：支持批量导入 GPT 账号密码与 2FA 密钥，自动持久化存储至 SQLite 本地数据库，提供专属「账号管理中心」列表界面，支持批量/单账号 OAuth 授权换取 Sub2API 凭据 (AT/RT/IDToken)、批量/单账号改密落库、实时 6 位 TOTP 动态码计算显示以及多格式导出。
> 整个 `tool` 文件夹已完全自包含，可直接拷贝或打包为 zip 发送给朋友使用！

---

## ✨ 核心特性

1. **SQLite 数据库持久化存储**
   - 导入的账号自动落库存储至 `tool/data.db`（SQLite WAL 高性能模式），数据永不丢失。
   - 系统参数、并发数设置、多行代理池配置均持久化在 `settings` 表中，页面刷新与重启服务后自动恢复。

2. **专属「账号管理中心」操作台**
   - **多维统计指标**：总账号数、Sub2API 就绪数 (AT✓/RT✓)、成功数、失败数、已改密数、2FA 30s 刷新周期倒计时环。
   - **表格列信息**：邮箱、当前密码、2FA 密钥与 **实时 6 位 TOTP 动态验证码（绿色大字一键复制）**、新密码、状态徽章、凭证就绪徽章 (AT✓/RT✓)、套餐类型 (Free/Plus/Team)、最新步骤与报错信息。
   - **单账号操作**：【⚡ 授权】、【📄 详细日志】、【🗑️ 删除】。

3. **批量与自动化流程**
   - **⚡ 批量 OAuth 授权并提取 Sub2API**：支持勾选指定账号或全量未授权账号，多线程并发过 Cloudflare 信任态、Sentinel PoW 挑战、账密验证与 2FA 校验，提取 1900+ 字符 AccessToken 以及 Codex 永久 RefreshToken (RT) 和 IDToken。
   - **🔑 批量与单账号修改密码**：支持保持原密码 / 统一自定义新密码 / 16位强随机密码 / 固定前缀+随机码，新密码自动持久化回写 SQLite 数据库。
   - **📄 实时时序日志流**：点开任意账号弹窗，即可实时流式查看该账号的初始化、CSRF、PoW 求解、密码校验、2FA 提交、会话回调与 Token 提取每一步详细日志，支持自动滚动与一键复制。

4. **多格式丰富导出**
   - 📦 **Sub2API JSON (`sub2api_accounts.json`)**：标准 Sub2API 格式，可直接在 Sub2API 网页面板一键批量导入。
   - 🌐 **ChatGPT 官方 Session JSON (`chatgpt_sessions.json`)**：完整 Session 结构体。
   - 📄 **已改密账密 2FA 文本**：`邮箱----新密码----2FA.txt`。
   - 📄 **已改密账密文本**：`邮箱----新密码.txt`。
   - 📄 **纯 AccessToken 列表 (`AT.txt`)** / **邮箱----AccessToken (`邮箱AT.txt`)** / **纯 RefreshToken (`RT.txt`)**。
   - ❌ **失败账号及原因清单**。

---

## 🚀 快速启动

### Windows 用户：
直接双击运行 `run.bat`，系统将自动拉起默认浏览器打开控制台页面（默认 `http://127.0.0.1:8899`）。

### Linux / macOS 用户：
终端中执行：
```bash
chmod +x run.sh
./run.sh
```

---

## 📦 如何打包给朋友使用

### 方式一：源码压缩包（最推荐）
将整个 `tool` 文件夹直接压缩为 `tool.zip` 发送给朋友。朋友解压后双击 `run.bat` 即可一键运行！

### 方式二：PyInstaller 打包为独立可执行程序 (.exe)
在 `tool` 目录下直接双击运行 `build_exe.bat`，打包完成后将在 `dist/GPT_2FA_Sub2API_Tool` 生成独立程序目录。
