"""利弗莫尔趋势捕捉器 — 系统配置"""
import os
from pathlib import Path

# 绕过系统代理设置，避免干扰 TuShare API 调用
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'
os.environ.pop('HTTP_PROXY', None)
os.environ.pop('HTTPS_PROXY', None)
os.environ.pop('http_proxy', None)
os.environ.pop('https_proxy', None)

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / 'data'
DAILY_DIR = DATA_DIR / 'daily'
RESULT_DIR = DATA_DIR / 'results'
DB_PATH = DATA_DIR / 'livermore_cache.db'

DATA_DIR.mkdir(parents=True, exist_ok=True)
DAILY_DIR.mkdir(parents=True, exist_ok=True)
RESULT_DIR.mkdir(parents=True, exist_ok=True)

# ── Tushare ──
TUSHARE_TOKEN = os.environ.get('TUSHARE_TOKEN', '')

# ── 均线参数 ──
MA_PERIODS = [10, 20, 50, 120, 200]
MA_MIN_PERIODS = 20

# ── 利弗莫尔关键点参数 ──
VOLUME_SURGE_RATIO = 1.5
ATH_MIN_HISTORY_DAYS = 120
ATH_FULL_HISTORY_DAYS = 250

# ── 关注池参数 ──
DEFAULT_STOP_LOSS_PCT = -0.07
DEFAULT_TAKE_PROFIT_PCT = 0.20
MAX_WATCH_POOL_SIZE = 50

# ── 并发参数 ──
N_WORKERS = min(8, os.cpu_count() or 4)
CHUNK_SIZE = 500

# ── 缓存 ──
CACHE_TTL_HOURS = 24
