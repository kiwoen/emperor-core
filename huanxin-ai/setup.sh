#!/bin/bash
# huanxin-ai v2.0.0 环境安装脚本 (Linux/macOS)

set -e

echo "========================================"
echo "  huanxin-ai v2.0.0 环境安装脚本"
echo "========================================"
echo

# 检测 Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] 未检测到 Python3。请安装 Python 3.11+ 后重试。"
    exit 1
fi

PYVER=$(python3 --version 2>&1 | awk '{print $2}')
echo "[OK] Python $PYVER"

# 创建虚拟环境
if [ ! -d "venv" ]; then
    echo "[INFO] 创建虚拟环境..."
    python3 -m venv venv
fi
echo "[OK] 虚拟环境就绪"

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "[INFO] 安装依赖..."
pip install -r requirements.txt --quiet || echo "[WARN] 部分依赖安装失败"

# 初始化数据库
echo "[INFO] 初始化数据库..."
python3 -c "from huanxin.database import init_db; init_db()" 2>/dev/null && echo "[OK] 数据库初始化完成" || echo "[WARN] 数据库初始化跳过"

# 运行测试
echo "[INFO] 运行基础测试..."
python3 -m pytest tests/ -x -q --tb=short 2>/dev/null && echo "[OK] 测试通过" || echo "[WARN] 部分测试未通过"

echo
echo "========================================"
echo "  安装完成！运行方式:"
echo "    python main.py --mode demo"
echo "    python main.py --mode chat"
echo "    python main.py --mode server"
echo "========================================"
