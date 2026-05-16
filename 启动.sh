#!/bin/bash
# 材料清单助手 · 本地执行平台 · 一键启动脚本

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "=========================================="
echo "  材料清单助手 · 本地执行平台"
echo "=========================================="
echo ""

# 1. 检查 Python3
if ! command -v python3 &>/dev/null; then
    echo "❌ 未找到 python3，请先安装 Python 3.9+"
    exit 1
fi
echo "✅ Python3: $(python3 --version)"

# 2. 检查依赖
echo "📦 检查依赖..."
MISSING=""
python3 -c "import flask" 2>/dev/null || MISSING="$MISSING flask"
python3 -c "import pandas" 2>/dev/null || MISSING="$MISSING pandas"
python3 -c "import openpyxl" 2>/dev/null || MISSING="$MISSING openpyxl"

if [ -n "$MISSING" ]; then
    echo "⚠️  缺少依赖:$MISSING"
    echo "📥 正在安装..."
    pip3 install flask pandas openpyxl
    echo "✅ 依赖安装完成"
else
    echo "✅ 依赖完整"
fi

# 3. 检查知识库数据（已内置在项目中）
KB_DIR="标准知识库/源数据"
if [ ! -d "$KB_DIR" ]; then
    echo "❌ 知识库目录不存在: $KB_DIR"
    echo "   请运行: python3 工具脚本/同步外部知识库.py"
    exit 1
fi
echo "✅ 知识库目录: $KB_DIR"

# 4. 启动服务
echo ""
echo "=========================================="
echo "  🚀 启动服务..."
echo "  访问地址: http://127.0.0.1:8765"
echo "  按 Ctrl+C 停止"
echo "=========================================="
echo ""

python3 服务端/主入口.py
