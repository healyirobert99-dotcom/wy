#!/bin/bash
set -e

echo "=== 利弗莫尔趋势捕捉器 — 部署脚本 ==="
echo ""

# 1. 安装依赖
echo "[1/4] 安装 Python 依赖..."
pip3 install -r requirements.txt --quiet

# 2. 创建目录结构
echo "[2/4] 创建数据目录..."
mkdir -p data/daily data/results logs

# 3. 复制 launchd 配置（macOS 开机自启）
echo "[3/4] 配置开机自启..."
cp com.stock.scanner.plist ~/Library/LaunchAgents/ 2>/dev/null || true
launchctl load ~/Library/LaunchAgents/com.stock.scanner.plist 2>/dev/null || true

# 4. 创建桌面快捷方式
echo "[4/4] 创建桌面快捷方式..."
cat > ~/Desktop/利弗莫尔趋势捕捉器.command << 'SCRIPT'
#!/bin/bash
cd "$(dirname "$0")/../stock_scanner" 2>/dev/null || cd ~/stock_scanner
STREAMLIT_EMAIL="" exec python3 -m streamlit run app.py --server.headless true --server.port 8502
SCRIPT
chmod +x ~/Desktop/利弗莫尔趋势捕捉器.command

echo ""
echo "✅ 部署完成！"
echo "   访问地址: http://localhost:8502"
echo "   后台启动: launchctl start com.stock.scanner"
echo "   手动启动: python3 -m streamlit run app.py --server.headless true --server.port 8502"
