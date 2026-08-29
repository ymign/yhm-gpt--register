#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "======================================================================"
echo "  ⚡ GPT 账号密码 2FA 管理与 Sub2API 转换助手"
echo "  开箱即用 · 批量改密 · 提取 Sub2API JSON 凭证"
echo "======================================================================"
echo ""

if ! command -v python3 &> /dev/null; then
    echo "[错误] 未检测到 python3，请先安装 Python 3.9+"
    exit 1
fi

echo "[1/2] 正在检查并安装依赖..."
pip3 install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet

echo "[2/2] 正在启动 Web 控制台..."
python3 app.py
