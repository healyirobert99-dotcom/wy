"""利弗莫尔趋势捕捉器 v2.0 — 专业金融仪表盘

全A股物理隔离计算 · 利弗莫尔关键点理论 · 深色专业主题
UI/UX Pro Max 设计系统 · Financial Dashboard 配色规范
"""
import os
import time
import streamlit as st
import pandas as pd
import numpy as np

from config import TUSHARE_TOKEN, RESULT_DIR
from database import init_db, query_scan_cache
from data_manager import (
    init_tushare, fetch_and_store_stock_basic, get_trade_days,
    download_daily_incremental, load_raw_data, load_stock_info, clean_pipeline,
)
from engine import parallel_ma_scan, parallel_trend_capture
from cache_manager import (
    generate_scan_id, flush_and_rewrite, update_trend_results,
    is_cache_valid, get_cache_summary,
    start_scan_log, finish_scan_log, fail_scan, trend_capture,
)
from tech_analysis import analyze_stock_tech, generate_analysis_html

# ═══════════════════════════════════════════════════════════
# 页面配置
# ═══════════════════════════════════════════════════════════

st.set_page_config(
    page_title="利弗莫尔趋势捕捉器",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ═══════════════════════════════════════════════════════════
# 设计系统 — Financial Dashboard 深色主题
# ═══════════════════════════════════════════════════════════

DESIGN_TOKENS = """
<style>
    /* ── 设计令牌 ── */
    :root {
        --bg-primary: #020617;
        --bg-surface: #0f172a;
        --bg-elevated: #1e293b;
        --bg-hover: #334155;
        --border-subtle: #1e293b;
        --border-default: #334155;
        --border-focus: #3b82f6;
        --text-primary: #f8fafc;
        --text-secondary: #94a3b8;
        --text-muted: #64748b;
        --accent-up: #22c55e;
        --accent-down: #ef4444;
        --accent-gold: #f59e0b;
        --accent-blue: #3b82f6;
        --accent-purple: #a855f7;
        --accent-cyan: #06b6d4;
        --accent-livermore: #f97316;
        --radius-sm: 6px;
        --radius-md: 10px;
        --radius-lg: 14px;
        --shadow-sm: 0 1px 2px rgba(0,0,0,0.3);
        --shadow-md: 0 4px 12px rgba(0,0,0,0.4);
        --shadow-glow-green: 0 0 20px rgba(34,197,94,0.15);
        --shadow-glow-red: 0 0 20px rgba(239,68,68,0.15);
    }

    /* ── 全局 ── */
    .stApp {
        background: var(--bg-primary);
        color: var(--text-primary);
        font-family: 'Noto Sans SC', 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .main .block-container {
        padding-top: 1.5rem;
        padding-bottom: 3rem;
        max-width: 1400px;
    }

    /* ── 侧边栏 ── */
    section[data-testid="stSidebar"] {
        background: var(--bg-surface);
        border-right: 1px solid var(--border-subtle);
    }
    section[data-testid="stSidebar"] .stButton button {
        width: 100%;
        border-radius: var(--radius-md);
        font-weight: 600;
        height: 46px;
        font-size: 0.9rem;
        letter-spacing: 0.3px;
        transition: all 0.2s ease;
    }
    section[data-testid="stSidebar"] .stButton button:hover {
        transform: translateY(-1px);
        box-shadow: var(--shadow-md);
    }
    section[data-testid="stSidebar"] .stButton button:disabled {
        opacity: 0.5;
        transform: none;
        box-shadow: none;
    }

    /* ── Tab ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background: var(--bg-surface);
        border-radius: var(--radius-lg);
        padding: 5px;
        border: 1px solid var(--border-subtle);
        margin-bottom: 1.5rem;
    }
    .stTabs [data-baseweb="tab"] {
        border-radius: var(--radius-md);
        padding: 10px 24px;
        color: var(--text-secondary);
        font-weight: 500;
        font-size: 0.85rem;
        letter-spacing: 0.2px;
        transition: all 0.2s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        color: var(--text-primary);
        background: var(--bg-elevated);
    }
    .stTabs [aria-selected="true"] {
        background: rgba(59,130,246,0.12) !important;
        color: #60a5fa !important;
        font-weight: 600;
    }

    /* ── 指标卡片 ── */
    .metric-card {
        background: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg);
        padding: 1.2rem 1.4rem;
        box-shadow: var(--shadow-sm);
        transition: all 0.25s ease;
        position: relative;
        overflow: hidden;
    }
    .metric-card::before {
        content: '';
        position: absolute;
        top: 0;
        left: 0;
        right: 0;
        height: 2px;
        background: var(--card-accent, var(--border-default));
        opacity: 0.6;
    }
    .metric-card:hover {
        border-color: var(--border-default);
        box-shadow: var(--shadow-md);
        transform: translateY(-2px);
    }
    .metric-card .label {
        color: var(--text-muted);
        font-size: 0.7rem;
        text-transform: uppercase;
        letter-spacing: 1.5px;
        margin-bottom: 6px;
        font-weight: 600;
    }
    .metric-card .value {
        font-size: 1.8rem;
        font-weight: 700;
        letter-spacing: -0.5px;
        line-height: 1.2;
    }
    .metric-card .value.gold  { color: var(--accent-gold); text-shadow: 0 0 20px rgba(245,158,11,0.2); }
    .metric-card .value.green { color: var(--accent-up); text-shadow: 0 0 20px rgba(34,197,94,0.2); }
    .metric-card .value.red   { color: var(--accent-down); text-shadow: 0 0 20px rgba(239,68,68,0.2); }
    .metric-card .value.blue  { color: #60a5fa; text-shadow: 0 0 20px rgba(96,165,250,0.2); }
    .metric-card .value.cyan  { color: var(--accent-cyan); text-shadow: 0 0 20px rgba(6,182,212,0.2); }
    .metric-card .sub {
        color: var(--text-muted);
        font-size: 0.75rem;
        margin-top: 6px;
    }

    /* ── 状态标签 ── */
    .tag {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 600;
        letter-spacing: 0.3px;
        line-height: 1.4;
    }
    .tag::before {
        content: '';
        width: 5px;
        height: 5px;
        border-radius: 50%;
        background: currentColor;
    }
    .tag-multi_head { background: rgba(34,197,94,0.1); color: var(--accent-up); border: 1px solid rgba(34,197,94,0.2); }
    .tag-short_bull { background: rgba(59,130,246,0.1); color: #60a5fa; border: 1px solid rgba(59,130,246,0.2); }
    .tag-neutral    { background: rgba(100,116,139,0.1); color: var(--text-secondary); border: 1px solid rgba(100,116,139,0.2); }
    .tag-bear       { background: rgba(239,68,68,0.1); color: var(--accent-down); border: 1px solid rgba(239,68,68,0.2); }
    .tag-weak       { background: rgba(248,113,113,0.1); color: #f87171; border: 1px solid rgba(248,113,113,0.2); }
    .tag-livermore  { background: rgba(249,115,22,0.1); color: var(--accent-livermore); border: 1px solid rgba(249,115,22,0.2); }

    /* ── 数据表格覆盖 ── */
    .stDataFrame {
        border-radius: var(--radius-lg) !important;
        overflow: hidden;
        border: 1px solid var(--border-subtle);
    }
    .stDataFrame > div:first-child {
        border-radius: var(--radius-lg) var(--radius-lg) 0 0 !important;
    }

    /* ── 标题区 ── */
    .app-title {
        font-size: 1.6rem;
        font-weight: 700;
        color: var(--text-primary);
        display: flex;
        align-items: center;
        gap: 10px;
        letter-spacing: -0.3px;
    }
    .app-title small {
        font-size: 0.8rem;
        color: var(--text-muted);
        font-weight: 500;
        background: var(--bg-elevated);
        padding: 2px 10px;
        border-radius: 20px;
        border: 1px solid var(--border-subtle);
    }
    .app-subtitle {
        color: var(--text-muted);
        font-size: 0.78rem;
        margin-top: 6px;
        letter-spacing: 0.2px;
    }
    .version-badge {
        display: inline-flex;
        align-items: center;
        gap: 4px;
        padding: 3px 10px;
        border-radius: 20px;
        font-size: 0.7rem;
        font-weight: 600;
        background: rgba(59,130,246,0.1);
        color: #60a5fa;
        border: 1px solid rgba(59,130,246,0.2);
    }

    /* ── 空状态 ── */
    .empty-state {
        text-align: center;
        padding: 4rem 1.5rem;
        background: var(--bg-surface);
        border: 1px solid var(--border-subtle);
        border-radius: var(--radius-lg);
    }
    .empty-state .icon {
        font-size: 3rem;
        margin-bottom: 0.8rem;
        opacity: 0.7;
    }
    .empty-state .title {
        font-size: 1.1rem;
        font-weight: 600;
        color: var(--text-secondary);
        margin-bottom: 6px;
    }
    .empty-state .text {
        font-size: 0.85rem;
        color: var(--text-muted);
        line-height: 1.5;
        max-width: 400px;
        margin: 0 auto;
    }
    .empty-state .hint {
        font-size: 0.75rem;
        color: var(--text-muted);
        margin-top: 12px;
        opacity: 0.7;
    }

    /* ── 分隔线 ── */
    hr.divider {
        margin: 1.5rem 0;
        border: 0;
        height: 1px;
        background: var(--border-subtle);
    }

    /* ── 进度条 (HTML 版) ── */
    .pb-bar {
        background: var(--bg-elevated);
        border-radius: 6px;
        height: 6px;
        overflow: hidden;
    }
    .pb-fill {
        height: 100%;
        border-radius: 6px;
        background: linear-gradient(90deg, #3b82f6, #60a5fa);
        transition: width 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        box-shadow: 0 0 10px rgba(59,130,246,0.3);
    }
    .pb-label {
        color: var(--text-secondary);
        font-size: 0.75rem;
        margin-bottom: 6px;
        font-weight: 500;
    }

    /* ── 结果通知 ── */
    .result-toast {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 1rem 1.2rem;
        border-radius: var(--radius-md);
        margin-bottom: 1.2rem;
        font-size: 0.9rem;
        border: 1px solid transparent;
    }
    .result-toast.success {
        background: rgba(34,197,94,0.08);
        border-color: rgba(34,197,94,0.2);
        color: #4ade80;
    }
    .result-toast.empty {
        background: rgba(100,116,139,0.08);
        border-color: rgba(100,116,139,0.2);
        color: var(--text-secondary);
    }

    /* ── 分布条形图 ── */
    .dist-bar-row {
        display: flex;
        align-items: center;
        gap: 12px;
        margin: 5px 0;
    }
    .dist-bar-label {
        width: 80px;
        font-size: 0.75rem;
        color: var(--text-secondary);
        text-align: right;
        font-weight: 500;
    }
    .dist-bar-track {
        flex: 1;
        height: 22px;
        background: var(--bg-elevated);
        border-radius: 5px;
        overflow: hidden;
        position: relative;
    }
    .dist-bar-fill {
        height: 100%;
        border-radius: 5px;
        opacity: 0.8;
        transition: width 0.6s cubic-bezier(0.4, 0, 0.2, 1);
    }
    .dist-bar-count {
        width: 50px;
        font-size: 0.82rem;
        color: var(--text-primary);
        font-weight: 600;
        text-align: right;
    }
    .dist-bar-pct {
        width: 45px;
        font-size: 0.7rem;
        color: var(--text-muted);
    }

    /* ── 页脚 ── */
    .app-footer {
        margin-top: 2.5rem;
        padding-top: 1.2rem;
        border-top: 1px solid var(--border-subtle);
        text-align: center;
        color: var(--text-muted);
        font-size: 0.68rem;
        letter-spacing: 0.3px;
    }

    /* ── 滚动条 ── */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: var(--bg-primary); }
    ::-webkit-scrollbar-thumb { background: var(--border-default); border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--text-muted); }

    /* ── Streamlit 组件深色覆盖 ── */
    .stAlert {
        border-radius: var(--radius-md) !important;
    }
    .stSpinner > div {
        border-color: #3b82f6 transparent transparent transparent !important;
    }
    .stProgress > div > div {
        background: linear-gradient(90deg, #3b82f6, #60a5fa) !important;
        border-radius: 4px !important;
    }

    /* ── 响应式 ── */
    @media (max-width: 768px) {
        .metric-card .value { font-size: 1.3rem; }
        .app-title { font-size: 1.2rem; flex-wrap: wrap; }
    }
</style>
"""

st.markdown(DESIGN_TOKENS, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# Session State 初始化
# ═══════════════════════════════════════════════════════════

for key in ('scan_ok', 'cache_summary', 'all_records', 'captured_records', 'watch_added', 'trend_ok',
            'selected_stock', 'analysis_result', 'analyzing'):
    if key not in st.session_state:
        st.session_state[key] = None

for key in ('scanning', 'capturing'):
    if key not in st.session_state:
        st.session_state[key] = False

init_db()


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

ALIGNMENT_LABELS = {
    'MULTI_HEAD': '标准多头',
    'SHORT_BULL': '短多',
    'NEUTRAL': '均线纠缠',
    'BEAR': '空头排列',
    'WEAK': '弱空头',
}

ALIGNMENT_COLORS = {
    'MULTI_HEAD': '#22c55e',
    'SHORT_BULL': '#3b82f6',
    'NEUTRAL': '#64748b',
    'BEAR': '#ef4444',
    'WEAK': '#f87171',
}


def make_tag(text, cls):
    return f'<span class="tag tag-{cls}">{text}</span>'


def alignment_tag(a):
    label = ALIGNMENT_LABELS.get(a, a)
    mapping = {
        'MULTI_HEAD': 'multi_head',
        'SHORT_BULL': 'short_bull',
        'NEUTRAL': 'neutral',
        'BEAR': 'bear',
        'WEAK': 'weak'
    }
    return make_tag(label, mapping.get(a, 'neutral'))


def card_html(label, value, sub="", accent="default"):
    """生成指标卡片 HTML."""
    accent_map = {
        "gold": "#f59e0b",
        "green": "#22c55e",
        "red": "#ef4444",
        "blue": "#3b82f6",
        "cyan": "#06b6d4",
        "default": "#334155",
    }
    color = accent_map.get(accent, accent_map["default"])
    sub_html = f'<div class="sub">{sub}</div>' if sub else ""
    return f"""
    <div class="metric-card" style="--card-accent: {color};">
        <div class="label">{label}</div>
        <div class="value {accent}">{value}</div>
        {sub_html}
    </div>
    """


# ═══════════════════════════════════════════════════════════
# 页面头部
# ═══════════════════════════════════════════════════════════

st.markdown("""
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:1.8rem;flex-wrap:wrap;gap:12px;">
    <div>
        <div class="app-title">
            📊 利弗莫尔趋势捕捉器
            <small>Livermore Trend Catcher</small>
            <span class="version-badge">v2.0</span>
        </div>
        <div class="app-subtitle">
            全A股 · MA多头排列 · ATH突破 · 放量确认 · 关键点交易
        </div>
    </div>
</div>
""", unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# 侧边栏
# ═══════════════════════════════════════════════════════════

with st.sidebar:
    st.markdown("""
    <div style="display:flex;align-items:center;gap:8px;margin-bottom:1.2rem;padding-bottom:1rem;border-bottom:1px solid var(--border-subtle);">
        <span style="font-size:1.3rem;">📡</span>
        <div>
            <div style="font-weight:600;color:var(--text-primary);font-size:0.9rem;">Tushare Pro</div>
            <div style="color:var(--accent-up);font-size:0.7rem;font-weight:600;display:flex;align-items:center;gap:4px;">
                <span style="width:6px;height:6px;border-radius:50%;background:var(--accent-up);display:inline-block;"></span>
                已连接
            </div>
        </div>
    </div>
    """, unsafe_allow_html=True)

    scan_btn = st.button(
        "🔍  全市场扫描",
        type="primary",
        use_container_width=True,
        disabled=st.session_state.scanning,
    )

    capture_btn = st.button(
        "🎯  趋势捕捉",
        use_container_width=True,
        disabled=st.session_state.capturing or st.session_state.scanning,
        help="从缓存中筛选利弗莫尔关键点信号（缓存为空时自动运行全市场扫描）",
    )

    st.markdown("<hr class='divider' style='margin:1.2rem 0;'>", unsafe_allow_html=True)

    progress_placeholder = st.markdown("""<div id="prog"></div>""", unsafe_allow_html=True)

    # 状态指示
    if st.session_state.scanning:
        st.info("⏳ 正在扫描全市场数据...", icon="🔍")
    elif st.session_state.capturing:
        st.info("⏳ 正在捕捉趋势信号...", icon="🎯")


def _pb(pct: int, text: str):
    """更新 sidebar 纯 HTML 进度条。"""
    progress_placeholder.markdown(f"""
    <div style="margin:16px 0;">
        <div class="pb-label">{text}</div>
        <div class="pb-bar"><div class="pb-fill" style="width:{pct}%;"></div></div>
    </div>
    """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# 全市场扫描逻辑
# ═══════════════════════════════════════════════════════════

if scan_btn:
    st.session_state.scanning = True
    st.session_state.scan_ok = None
    st.session_state.trend_ok = None
    scan_id = generate_scan_id()

    try:
        _pb(0, "初始化数据引擎...")
        pro = init_tushare()
        start_scan_log(scan_id, 'FULL_SCAN')

        _pb(5, "获取交易日历...")
        trade_days = get_trade_days(pro, n=500)
        _pb(5, f"交易日: {len(trade_days)} 天")

        _pb(10, "获取全市场股票列表...")
        stock_info = fetch_and_store_stock_basic(pro)

        _pb(10, f"全市场 {len(stock_info)} 只标的")

        def dl_cb(i, total, day):
            pct = 10 + int((i + 1) / total * 60)
            _pb(min(pct, 70), f"下载 {day} ({i+1}/{total})")

        downloaded, rows = download_daily_incremental(pro, trade_days, dl_cb)
        _pb(70, f"数据就绪 ({downloaded} 天, {rows} 行)")

        _pb(70, "加载并清洗数据...")
        ma_days = trade_days[-250:] if len(trade_days) > 250 else trade_days
        all_data = load_raw_data(ma_days)
        stock_info_df = load_stock_info()
        all_data, clean_stats = clean_pipeline(all_data, stock_info, ma_days)

        info_cols = stock_info_df[['ts_code', 'name', 'industry']].drop_duplicates('ts_code')
        all_data = all_data.merge(info_cols, on='ts_code', how='left')
        all_data['name'] = all_data['name'].fillna('')
        all_data['industry'] = all_data['industry'].fillna('')

        if 'adj_factor' in all_data.columns:
            all_data['close_adj'] = all_data['close'] * all_data['adj_factor']
        else:
            all_data['close_adj'] = all_data['close']

        _pb(75, f"清洗完成: {clean_stats['total_after']} 只")

        _pb(75, "多线程 MA 排列扫描...")

        def scan_cb(c, t):
            if t == 1:
                _pb(90 if c == 0 else 95, "MA 计算中..." if c == 0 else "MA 计算完成")
            else:
                pct = 75 + int(c / t * 20)
                _pb(min(pct, 95), f"MA 计算中... {c}/{t} 块")

        results = parallel_ma_scan(all_data, scan_cb)
        _pb(95, f"MA 计算完成: {len(results)} 只")

        _pb(95, "写入缓存...")
        flush_and_rewrite(results, scan_id)

        st.session_state.cache_summary = get_cache_summary()
        s = st.session_state.cache_summary

        finish_scan_log(scan_id, {
            'total_stocks': clean_stats['total_after'],
            'ma_aligned_count': s.get('ma_aligned', 0),
            'duration_seconds': 0,
        })

        _pb(100, "全市场扫描完成")
        st.toast("✅ 全市场扫描完成", icon="✅")
        st.session_state.scan_ok = True
        st.session_state.all_records = results
        st.session_state.captured_records = None
        st.session_state.scanning = False

    except Exception as e:
        st.session_state.scan_ok = False
        st.session_state.scanning = False
        fail_scan(scan_id, str(e))
        st.error(f"扫描失败: {e}")


# ═══════════════════════════════════════════════════════════
# 趋势捕捉逻辑 (阶段二)
# ═══════════════════════════════════════════════════════════

if capture_btn:
    st.session_state.capturing = True

    try:
        cache_records = query_scan_cache(multi_head_only=True)
        if not cache_records:
            st.warning("缓存为空，请先点击「全市场扫描」完成数据扫描后再进行操作。")
            st.session_state.trend_ok = True
            st.session_state.watch_added = 0
            st.session_state.capturing = False
            st.stop()

        scan_id = generate_scan_id()
        start_scan_log(scan_id, 'TREND_CAPTURE')

        ma_aligned_codes = [r['ts_code'] for r in cache_records]

        _pb(0, "加载日线数据...")
        trade_days = get_trade_days(init_tushare(), n=500)
        all_data = load_raw_data(trade_days)
        stock_info_df = load_stock_info()

        info_cols = stock_info_df[['ts_code', 'name', 'industry']].drop_duplicates('ts_code')
        all_data = all_data.merge(info_cols, on='ts_code', how='left')
        all_data['name'] = all_data['name'].fillna('')
        all_data['industry'] = all_data['industry'].fillna('')

        if 'adj_factor' in all_data.columns:
            all_data['close_adj'] = all_data['close'] * all_data['adj_factor']
        else:
            all_data['close_adj'] = all_data['close']

        _pb(30, f"已加载 {len(trade_days)} 天数据")

        _pb(30, "计算 ATH / 量比 / 关键点信号...")

        def cap_cb(c, t):
            if t == 1:
                _pb(70 if c == 0 else 85, "趋势计算中..." if c == 0 else "趋势计算完成")
            else:
                pct = 30 + int(c / t * 50)
                _pb(min(pct, 80), f"趋势计算中... {c}/{t} 块")

        trend_results = parallel_trend_capture(all_data, ma_aligned_codes, cap_cb)
        _pb(85, f"趋势计算完成: {len(trend_results)} 只")

        _pb(85, "更新缓存...")
        update_trend_results(trend_results, scan_id)

        added = trend_capture(scan_id, trend_results, max_pool_size=50)
        st.session_state.watch_added = len(added)
        st.session_state.trend_ok = True

        st.session_state.cache_summary = get_cache_summary()

        finish_scan_log(scan_id, {
            'total_stocks': len(cache_records),
            'ma_aligned_count': len(ma_aligned_codes),
            'ath_break_count': st.session_state.cache_summary.get('ath_break', 0),
            'volume_surge_count': st.session_state.cache_summary.get('volume_surge', 0),
            'livermore_count': st.session_state.cache_summary.get('livermore', 0),
            'watch_candidates': len(added),
            'duration_seconds': 0,
        })

        st.session_state.captured_records = trend_results
        _pb(100, "趋势捕捉完成")
        st.toast(f"✅ 趋势捕捉完成，{len(added)} 只标的入池", icon="🎯")
        st.session_state.capturing = False

    except Exception as e:
        st.error(f"趋势捕捉失败: {e}")
        st.session_state.capturing = False


# ═══════════════════════════════════════════════════════════
# 主内容区 — 双 Tab
# ═══════════════════════════════════════════════════════════

cache_summary = st.session_state.cache_summary or (get_cache_summary() if is_cache_valid() else None)

tabs = st.tabs(["📈 全市场扫描", "🎯 趋势捕捉"])


# ═══════════════════════════════════════════════════════════
# TAB 1: 全市场扫描
# ═══════════════════════════════════════════════════════════

with tabs[0]:
    if cache_summary:
        s = cache_summary

        # 指标卡片行
        col1, col2, col3, col4, col5 = st.columns(5)
        with col1:
            st.markdown(card_html(
                "已扫描", s['total'], "", "gold"
            ), unsafe_allow_html=True)
        with col2:
            ratio = round(s['ma_aligned'] / max(s['total'], 1) * 100, 1)
            st.markdown(card_html(
                "MA 多头排列", s['ma_aligned'], f"占比 {ratio}%", "green"
            ), unsafe_allow_html=True)
        with col3:
            st.markdown(card_html(
                "ATH 突破", s['ath_break'], "", "blue"
            ), unsafe_allow_html=True)
        with col4:
            st.markdown(card_html(
                "成交量放量", s['volume_surge'], "", "cyan"
            ), unsafe_allow_html=True)
        with col5:
            st.markdown(card_html(
                "★ 利弗莫尔信号", s['livermore'], "", "gold"
            ), unsafe_allow_html=True)

        st.markdown('<hr class="divider">', unsafe_allow_html=True)

        # MA 排列分布
        st.markdown(
            '<div style="font-weight:600;color:var(--text-primary);margin-bottom:12px;font-size:0.95rem;">'
            'MA 排列分布</div>',
            unsafe_allow_html=True
        )

        alignment_counts = {'MULTI_HEAD': 0, 'SHORT_BULL': 0, 'NEUTRAL': 0, 'BEAR': 0, 'WEAK': 0}
        records = st.session_state.all_records or query_scan_cache()
        for r in records:
            a = r.get('ma_alignment', 'NEUTRAL')
            if a in alignment_counts:
                alignment_counts[a] += 1

        if sum(alignment_counts.values()) > 0:
            chart_data = pd.DataFrame({
                '排列状态': [ALIGNMENT_LABELS.get(k, k) for k in alignment_counts.keys()],
                '数量': list(alignment_counts.values()),
                '颜色': list(ALIGNMENT_COLORS.values()),
                'slug': list(alignment_counts.keys()),
            })
            chart_data = chart_data[chart_data['数量'] > 0]
            total = chart_data['数量'].sum()

            bars = []
            for _, row in chart_data.iterrows():
                pct = row['数量'] / total * 100
                bars.append(
                    f'<div class="dist-bar-row">'
                    f'<div class="dist-bar-label">{row["排列状态"]}</div>'
                    f'<div class="dist-bar-track">'
                    f'<div class="dist-bar-fill" style="width:{pct}%;background:{row["颜色"]};"></div>'
                    f'</div>'
                    f'<div class="dist-bar-count">{row["数量"]}</div>'
                    f'<div class="dist-bar-pct">{pct:.1f}%</div>'
                    f'</div>'
                )
            st.markdown(''.join(bars), unsafe_allow_html=True)

        st.markdown('<hr class="divider">', unsafe_allow_html=True)

        # 多头列表 — 使用原生 st.dataframe 以获得排序和交互
        st.markdown(
            '<div style="font-weight:600;color:var(--text-primary);margin-bottom:12px;font-size:0.95rem;">'
            'MA 多头排列标的</div>',
            unsafe_allow_html=True
        )

        bull_records = [r for r in records if r.get('ma_alignment') in ('MULTI_HEAD', 'SHORT_BULL')]

        if bull_records:
            df = pd.DataFrame(bull_records)
            # 构建展示 DataFrame —— 保留代码/名称/行业，新增有参考性的技术面指标
            close_adj = pd.to_numeric(df.get('close_adj', pd.Series(0, index=df.index)), errors='coerce')
            ma_10 = pd.to_numeric(df.get('ma_10', pd.Series(0, index=df.index)), errors='coerce')
            ma_20 = pd.to_numeric(df.get('ma_20', pd.Series(0, index=df.index)), errors='coerce')
            ma_50 = pd.to_numeric(df.get('ma_50', pd.Series(0, index=df.index)), errors='coerce')
            ma_120 = pd.to_numeric(df.get('ma_120', pd.Series(0, index=df.index)), errors='coerce')
            ma_200 = pd.to_numeric(df.get('ma_200', pd.Series(0, index=df.index)), errors='coerce')
            ath_price = pd.to_numeric(df.get('ath_price', pd.Series(np.nan, index=df.index)), errors='coerce')
            vol_ratio = pd.to_numeric(df.get('vol_ratio', pd.Series(0, index=df.index)), errors='coerce').fillna(0)

            display_df = pd.DataFrame({
                '代码': df.get('ts_code', ''),
                '名称': df.apply(lambda r: (r.get('name') or '') or r.get('ts_code', ''), axis=1),
                '行业': df.get('industry', '').fillna('') if 'industry' in df.columns else '',
                '现价': close_adj.round(2),
                '排列': df.get('ma_alignment', 'NEUTRAL').map(ALIGNMENT_LABELS),
                '短期偏离%': ((close_adj - ma_10) / ma_10 * 100).round(2),
                '中期偏离%': ((close_adj - ma_50) / ma_50 * 100).round(2),
                'MA斜率%': ((ma_10 - ma_20) / ma_20 * 100).round(2),
                '距ATH%': ((ath_price - close_adj) / ath_price * 100).round(2),
                '量比': vol_ratio.round(2),
            })
            # 信号列：综合判断
            def _signal_label(row):
                if row.get('is_livermore') == 1:
                    return '🔥关键点'
                if row.get('is_ath_break') == 1:
                    return 'ATH突破'
                if row.get('is_volume_surge') == 1:
                    return '放量'
                return ''
            display_df['信号'] = df.apply(_signal_label, axis=1)
            display_df = display_df.sort_values('量比', ascending=False).reset_index(drop=True)

            st.dataframe(
                display_df,
                use_container_width=True,
                height=420,
                column_config={
                    '代码': st.column_config.TextColumn('代码', width='small'),
                    '名称': st.column_config.TextColumn('名称', width='medium'),
                    '行业': st.column_config.TextColumn('行业', width='medium'),
                    '现价': st.column_config.NumberColumn('现价', format='%.2f', width='small'),
                    '排列': st.column_config.TextColumn('排列', width='small'),
                    '短期偏离%': st.column_config.NumberColumn('短期偏离%', format='%.2f%%', width='small'),
                    '中期偏离%': st.column_config.NumberColumn('中期偏离%', format='%.2f%%', width='small'),
                    'MA斜率%': st.column_config.NumberColumn('MA斜率%', format='%.2f%%', width='small'),
                    '距ATH%': st.column_config.NumberColumn('距ATH%', format='%.2f%%', width='small'),
                    '量比': st.column_config.NumberColumn('量比', format='%.2f', width='small'),
                    '信号': st.column_config.TextColumn('信号', width='small'),
                },
                hide_index=True,
            )
        else:
            st.markdown("""
            <div class="empty-state">
                <div class="icon">📭</div>
                <div class="title">暂无 MA 多头排列标的</div>
                <div class="text">当前市场环境下未检测到符合条件的标的，请稍后重试或调整参数。</div>
            </div>
            """, unsafe_allow_html=True)

    else:
        st.markdown("""
        <div class="empty-state">
            <div class="icon">📊</div>
            <div class="title">开始全市场分析</div>
            <div class="text">点击左侧「全市场扫描」按钮，系统将分析全 A 股 MA 排列状态。</div>
            <div class="hint">全A股物理隔离计算 · MA10/20/50/120/200 五层均线判定</div>
        </div>
        """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# TAB 2: 趋势捕捉
# ═══════════════════════════════════════════════════════════

with tabs[1]:
    # 完成通知
    if st.session_state.trend_ok:
        added = st.session_state.watch_added
        if added and added > 0:
            st.markdown(f"""
            <div class="result-toast success">
                <span style="font-size:1.2rem;">✅</span>
                <div>
                    <div style="font-weight:600;">趋势捕捉完成</div>
                    <div style="font-size:0.8rem;opacity:0.8;">{added} 只标的触发利弗莫尔关键点信号</div>
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="result-toast empty">
                <span style="font-size:1.2rem;">📭</span>
                <div>
                    <div style="font-weight:600;">暂无符合条件的标的</div>
                    <div style="font-size:0.8rem;opacity:0.8;">当前无 MA多头排列 + ATH突破 + 放量确认 的共振信号</div>
                </div>
            </div>
            """, unsafe_allow_html=True)

    # 利弗莫尔信号列表
    captured = st.session_state.captured_records
    livermore_records = [r for r in (captured or []) if r.get('is_livermore') == 1]

    if livermore_records:
        st.markdown(
            f'<div style="font-weight:600;color:var(--accent-livermore);margin-bottom:12px;font-size:0.95rem;">'
            f'★ 利弗莫尔关键点信号 ({len(livermore_records)} 只)</div>',
            unsafe_allow_html=True
        )

        df = pd.DataFrame(livermore_records)
        cache_map = {r['ts_code']: r.get('ma_alignment', '') for r in (st.session_state.all_records or [])}
        df['ma_alignment'] = df['ts_code'].map(cache_map)

        close_adj = pd.to_numeric(df.get('close_adj', pd.Series(0, index=df.index)), errors='coerce').fillna(0)
        ath_price = pd.to_numeric(df.get('ath_price', pd.Series(0, index=df.index)), errors='coerce').fillna(0)
        vol_ratio = pd.to_numeric(df.get('vol_ratio', pd.Series(0, index=df.index)), errors='coerce').fillna(0)
        break_pct = ((close_adj / ath_price.clip(lower=0.001) - 1) * 100).round(2)

        def _signal_strength(row):
            score = 0
            close_adj = row.get('close_adj', 0) or 0
            ath = row.get('ath_price', 0) or 0
            vr = row.get('vol_ratio', 0) or 0
            align = row.get('ma_alignment', '')
            if ath > 0 and close_adj > 0:
                bp = (close_adj / ath - 1) * 100
                if bp > 0:
                    score += min(bp * 2, 30)
            if vr > 1.5:
                score += 25
            elif vr > 1.0:
                score += 15
            if align == 'MULTI_HEAD':
                score += 20
            elif align == 'SHORT_BULL':
                score += 10
            if score >= 60:
                return '🔥🔥🔥 极强'
            elif score >= 40:
                return '🔥🔥 强'
            elif score >= 20:
                return '🔥 中'
            return '—'

        display_df = pd.DataFrame({
            '代码': df.get('ts_code', ''),
            '名称': df.apply(lambda r: (r.get('name') or '') or r.get('ts_code', ''), axis=1),
            '行业': df.get('industry', '').fillna('') if 'industry' in df.columns else '',
            '现价': close_adj.round(2),
            'ATH': ath_price.round(2),
            '突破比例': break_pct,
            '量比': vol_ratio.round(2),
            '排列': df['ma_alignment'].map(ALIGNMENT_LABELS),
            '信号强度': df.apply(_signal_strength, axis=1),
        })
        display_df = display_df.sort_values('量比', ascending=False).reset_index(drop=True)

        st.dataframe(
            display_df,
            use_container_width=True,
            height=380,
            column_config={
                '代码': st.column_config.TextColumn('代码', width='small'),
                '名称': st.column_config.TextColumn('名称', width='medium'),
                '行业': st.column_config.TextColumn('行业', width='medium'),
                '现价': st.column_config.NumberColumn('现价', format='%.2f', width='small'),
                'ATH': st.column_config.NumberColumn('ATH', format='%.2f', width='small'),
                '突破比例': st.column_config.NumberColumn('突破比例', format='+%.2f%%', width='small'),
                '量比': st.column_config.NumberColumn('量比', format='%.2f', width='small'),
                '排列': st.column_config.TextColumn('排列', width='small'),
                '信号强度': st.column_config.TextColumn('信号强度', width='small'),
            },
            hide_index=True,
        )

        # ── 个股技术面深度分析 ──
        st.markdown('<hr class="divider">', unsafe_allow_html=True)
        st.markdown(
            '<div style="font-weight:600;color:var(--text-primary);margin-bottom:12px;font-size:0.95rem;">'
            '🔬 个股技术面深度分析</div>',
            unsafe_allow_html=True
        )

        stock_options = [f"{r.get('name', r['ts_code'])} ({r['ts_code']})" for r in livermore_records]
        selected = st.selectbox("选择标的", stock_options, key="stock_selector")

        analyze_col1, analyze_col2 = st.columns([1, 4])
        with analyze_col1:
            analyze_btn = st.button("生成分析报告", type="primary", use_container_width=True)
        with analyze_col2:
            if st.session_state.analyzing:
                st.info("⏳ 正在调用多维度技术面分析，请稍候...", icon="🔬")

        if analyze_btn:
            st.session_state.analyzing = True
            st.session_state.selected_stock = selected
            st.session_state.analysis_result = None
            st.rerun()

        if st.session_state.analyzing and st.session_state.selected_stock:
            selected_ts_code = st.session_state.selected_stock.split('(')[-1].rstrip(')')
            selected_name = st.session_state.selected_stock.split('(')[0].strip()
            try:
                result = analyze_stock_tech(selected_ts_code, selected_name)
                st.session_state.analysis_result = result
            except Exception as e:
                st.error(f"分析失败: {e}")
            st.session_state.analyzing = False
            st.rerun()

        if st.session_state.analysis_result:
            data = st.session_state.analysis_result
            quote = data.get("quote") or {}
            em_ind = data.get("em_indicators", {})
            em_sig = data.get("em_signals", {})
            qt_tech = data.get("qt_tech", {})
            dragon = data.get("dragon", {})
            risk = data.get("risk", {})
            sentiment = data.get("sentiment", {})
            rating = data.get("rating", {})
            name = data.get("name", "")
            ts_code = data.get("ts_code", "")
            price = quote.get("price", 0)
            change_pct = quote.get("change_pct", 0)

            if data.get("error"):
                st.warning(f"⚠️ {data['error']}")

            # ── 头部：名称+评级 ──
                h1, h2 = st.columns([3, 1])
                with h1:
                    st.markdown(f"**{name}** `{ts_code}`")
                    c_color = "#22c55e" if change_pct >= 0 else "#ef4444"
                    c_sign = "+" if change_pct >= 0 else ""
                    st.markdown(
                        f"<span style='font-size:1.2rem;font-weight:700;'>{price:.2f}</span> "
                        f"<span style='color:{c_color};font-weight:600;'>" f"{c_sign}{change_pct:.2f}%</span> "
                        f"<span style='color:#64748b;font-size:0.8rem;'>| 换手 {quote.get('turnover', '--')}% | 量比 {qt_tech.get('volume_ratio', '--')}</span>",
                        unsafe_allow_html=True,
                    )
                    # 共振提示
                    if rating.get("resonance"):
                        reasons_text = " + ".join(rating.get("resonance_reasons", []))
                        st.markdown(
                            f"<div style='margin-top:6px;'>"
                            f"<span style='display:inline-block;padding:3px 10px;border-radius:6px;background:#fde68a;color:#854d0e;font-size:0.8rem;font-weight:700;border:1px solid #f59e0b;'>"
                            f"多因子共振</span>　"
                            f"<span style='font-size:0.75rem;color:#64748b;'>{reasons_text}</span>"
                            f"</div>",
                            unsafe_allow_html=True,
                        )
                with h2:
                    grade = rating.get("grade", "C")
                    grade_label = rating.get("grade_label", "中性观望")
                    grade_color = rating.get("grade_color", "#f59e0b")
                    score = rating.get("score", 50)
                    st.markdown(
                        f"<div style='text-align:right;'>"
                        f"<div style='font-size:2rem;font-weight:800;color:{grade_color};line-height:1;'>{grade}</div>"
                        f"<div style='font-size:0.8rem;color:{grade_color};font-weight:600;'>{grade_label}</div>"
                        f"<div style='font-size:0.7rem;color:#64748b;'>综合评分 {score}/100</div>"
                        f"</div>",
                        unsafe_allow_html=True,
                    )

                st.divider()

                # ── 技术指标卡片 ──
                st.markdown("**主要技术指标**")
                if not em_ind:
                    st.info("技术指标数据暂不可用。若部署在 Streamlit Cloud，请确认 Settings → Secrets 中已配置 TUSHARE_TOKEN。")
                c1, c2, c3, c4 = st.columns(4)
                kdj = em_ind.get("kdj", {})
                kdj_status = em_sig.get("kdj", {}).get("status", "--")
                kdj_cross = "金叉" if em_sig.get("kdj", {}).get("signal") == 1 else ("死叉" if em_sig.get("kdj", {}).get("signal") == -1 else "无交叉")
                with c1:
                    with st.container(border=True):
                        st.caption("KDJ")
                        st.markdown(f"**K** {kdj.get('k') or '--'}　**D** {kdj.get('d') or '--'}　**J** {kdj.get('j') or '--'}")
                        st.markdown(f"<span style='font-size:0.75rem;color:#94a3b8;'>{kdj_status} | {kdj_cross}</span>", unsafe_allow_html=True)

                macd = em_ind.get("macd", {})
                macd_trend = em_sig.get("macd", {}).get("trend", "--")
                macd_cross = "金叉" if em_sig.get("macd", {}).get("signal") == 1 else ("死叉" if em_sig.get("macd", {}).get("signal") == -1 else "无交叉")
                with c2:
                    with st.container(border=True):
                        st.caption("MACD")
                        st.markdown(f"**DIF** {macd.get('dif') or '--'}　**DEA** {macd.get('dea') or '--'}")
                        st.markdown(f"<span style='font-size:0.75rem;color:#94a3b8;'>{macd_trend} | {macd_cross}</span>", unsafe_allow_html=True)

                boll = em_ind.get("boll", {})
                boll_pos = em_sig.get("boll", "--")
                with c3:
                    with st.container(border=True):
                        st.caption("布林带")
                        st.markdown(f"**上** {boll.get('upper') or '--'}　**中** {boll.get('middle') or '--'}")
                        st.markdown(f"<span style='font-size:0.75rem;color:#94a3b8;'>股价位置: {boll_pos}</span>", unsafe_allow_html=True)

                ma = em_ind.get("ma", {})
                ma_trend = qt_tech.get("ma_trend", "--")
                with c4:
                    with st.container(border=True):
                        st.caption("均线")
                        st.markdown(f"**MA5** {ma.get('ma5') or '--'}　**MA10** {ma.get('ma10') or '--'}")
                        st.markdown(f"<span style='font-size:0.75rem;color:#94a3b8;'>趋势: {ma_trend}</span>", unsafe_allow_html=True)

                st.divider()

                # ── 信号 + 风险 + 情绪 ──
                s1, s2, s3 = st.columns(3)
                with s1:
                    st.markdown("**龙头战法信号**")
                    dragon_signals = dragon.get("signals", [])
                    if dragon_signals:
                        for sig in dragon_signals[:3]:
                            lvl = sig.get("level", 1)
                            fire = "🔥" * lvl
                            st.info(f"{fire} **{sig.get('type', '')}** — {sig.get('action', '')}\n\n*{sig.get('desc', '')}*")
                    else:
                        st.markdown("<span style='color:#64748b;font-size:0.85rem;'>无明显龙头战法信号</span>", unsafe_allow_html=True)

                with s2:
                    st.markdown("**风险检查**")
                    risk_score = risk.get("risk_score", 100)
                    risk_level = risk.get("risk_level", "安全")
                    r_color = "#22c55e" if risk_level == "安全" else ("#f59e0b" if risk_level == "中等" else "#ef4444")
                    st.markdown(f"<div style='font-size:1.5rem;font-weight:700;color:{r_color};'>{risk_score}</div>", unsafe_allow_html=True)
                    st.markdown(f"<span style='color:{r_color};font-weight:600;'>{risk_level}</span>", unsafe_allow_html=True)
                    for r in risk.get("risks", ["✅ 无明显风险"]):
                        st.markdown(f"<div style='font-size:0.8rem;color:#94a3b8;'>{r}</div>", unsafe_allow_html=True)

                with s3:
                    st.markdown("**市场情绪**")
                    sentiment_phase = sentiment.get("phase", "未知")
                    sentiment_score = sentiment.get("score", 50)
                    se_color = "#22c55e" if sentiment_score >= 55 else ("#f59e0b" if sentiment_score >= 40 else "#ef4444")
                    st.markdown(f"<div style='font-size:1.2rem;font-weight:700;color:{se_color};'>{sentiment_phase}</div>", unsafe_allow_html=True)
                    st.markdown(f"<span style='font-size:0.8rem;color:#94a3b8;'>情绪评分 {sentiment_score}/100</span>", unsafe_allow_html=True)
                    st.markdown(f"<div style='font-size:0.75rem;color:#64748b;margin-top:4px;'>{sentiment.get('suggestion', '')}</div>", unsafe_allow_html=True)

                st.divider()

                # ── 综合判断 ──
                action = rating.get("action", "观望")
                action_color = rating.get("action_color", "#f59e0b")
                reasons = rating.get("reasons", [])
                stop_loss = rating.get("stop_loss")
                st.markdown(
                    f"**综合判断：{action}**　<span style='color:#64748b;font-size:0.85rem;'>— 基于技术面综合研判</span>",
                    unsafe_allow_html=True,
                )
                for rs in reasons[:8]:
                    st.markdown(f"- {rs}")
                if stop_loss:
                    st.error(f"建议止损位: **{stop_loss} 元**")
                st.caption("风险提示：以上内容仅为基于历史数据和技术指标的分析，不构成任何投资建议。股市有风险，投资需谨慎。")
    else:
        if is_cache_valid():
            st.markdown("""
            <div class="empty-state">
                <div class="icon">🎯</div>
                <div class="title">暂无利弗莫尔关键点信号</div>
                <div class="text">点击左侧「趋势捕捉」从缓存中筛选符合共振条件的标的。</div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div class="empty-state">
                <div class="icon">🔍</div>
                <div class="title">请先执行全市场扫描</div>
                <div class="text">系统需要先获取全市场数据，才能进行趋势捕捉分析。</div>
            </div>
            """, unsafe_allow_html=True)


# ═══════════════════════════════════════════════════════════
# 页脚
# ═══════════════════════════════════════════════════════════

st.markdown("""
<div class="app-footer">
    利弗莫尔趋势捕捉器 v2.0 · 基于杰西·利弗莫尔关键点理论 · 数据来源 Tushare Pro · UI/UX Pro Max 设计系统
</div>
""", unsafe_allow_html=True)
