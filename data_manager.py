"""利弗莫尔趋势捕捉器 — 数据获取与清洗管道

数据流:
  Tushare API → 原始 DataFrame → 清洗管道 → 清洗后数据 → SQLite

清洗管道 (按顺序):
  1. ST / *ST / 退市 剔除
  2. 当日停牌剔除
  3. 新股/次新股开板前过滤 (上市不足阈值)
  4. 北交所选择性剔除
"""
import time
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional, Callable, List, Tuple

import tushare as ts

from config import (
    TUSHARE_TOKEN, DAILY_DIR, ATH_MIN_HISTORY_DAYS, ATH_FULL_HISTORY_DAYS
)
from database import (
    get_db, upsert_stock_basic, batch_insert_daily, get_active_stocks,
    get_daily_data, init_db
)


def init_tushare() -> ts.pro_api:
    """初始化 Tushare API."""
    ts.set_token(TUSHARE_TOKEN)
    return ts.pro_api()


def fetch_and_store_stock_basic(pro) -> pd.DataFrame:
    """获取全市场股票列表, 标记ST, 写入 stock_basic 表."""
    df = pro.stock_basic(
        exchange='',
        list_status='L',
        fields='ts_code,symbol,name,area,industry,market,exchange,list_status,list_date,is_hs'
    )
    if df is None or df.empty:
        raise RuntimeError("无法获取股票列表, 请检查 Tushare 连接和 Token。")

    # 标记 ST / *ST
    df['is_st'] = df['name'].str.contains(r'ST|\*ST|退', na=False).astype(int)

    # 写入数据库
    upsert_stock_basic(df.to_dict('records'))

    return df


def fetch_and_store_suspended(pro, trade_date: str) -> set:
    """获取当日停牌股票列表, 返回停牌 ts_code 集合."""
    try:
        df = pro.suspend(suspend_date=trade_date)
        if df is not None and not df.empty:
            suspended = set(df['ts_code'].tolist())
            # 更新 stock_basic 停牌标记
            with get_db() as conn:
                conn.executemany(
                    "UPDATE stock_basic SET is_suspended = 1, updated_at = datetime('now') WHERE ts_code = ?",
                    [(c,) for c in suspended]
                )
            return suspended
    except Exception:
        pass
    return set()


def fetch_and_store_adj_factors(pro, trade_dates: List[str]):
    """获取复权因子并写入 daily_prices."""
    for date in trade_dates:
        try:
            df = pro.adj_factor(trade_date=date)
            if df is not None and not df.empty:
                with get_db() as conn:
                    for _, row in df.iterrows():
                        conn.execute(
                            "UPDATE daily_prices SET adj_factor = ? WHERE ts_code = ? AND trade_date = ?",
                            (float(row['adj_factor']), row['ts_code'], date)
                        )
            time.sleep(0.15)
        except Exception:
            pass


def download_daily_incremental(
    pro,
    trade_days: List[str],
    progress_callback: Optional[Callable] = None,
) -> Tuple[int, int]:
    """增量下载日线数据到 CSV 缓存, 同时写入 SQLite.

    Args:
        trade_days: 交易日列表 (YYYYMMDD)
        progress_callback: (current, total, date) 进度回调

    Returns:
        (下载的交易日数, 总行数)
    """
    downloaded_days = 0
    total_rows = 0

    for i, day in enumerate(trade_days):
        if progress_callback:
            progress_callback(i, len(trade_days), day)

        fpath = DAILY_DIR / f"{day}.csv"
        if fpath.exists() and fpath.stat().st_size > 0:
            continue

        df = pro.daily(trade_date=day)
        if df is not None and not df.empty:
            # 写入 CSV 缓存
            df.to_csv(fpath, index=False, encoding='utf-8-sig')

            # 写入 SQLite daily_prices
            records = df.to_dict('records')
            for r in records:
                r.setdefault('adj_factor', 1.0)  # Tushare daily 不含复权因子, 默认1.0
            batch_insert_daily(records)
            total_rows += len(records)
            downloaded_days += 1

        time.sleep(0.25)  # 频率限制

    return downloaded_days, total_rows


# ── 清洗管道 ──

def load_raw_data(trade_days: List[str]) -> pd.DataFrame:
    """从 CSV 缓存加载原始日线数据."""
    frames = []
    for day in trade_days:
        fpath = DAILY_DIR / f"{day}.csv"
        if fpath.exists():
            df = pd.read_csv(fpath)
            if not df.empty:
                frames.append(df)

    if not frames:
        raise RuntimeError("没有可用的行情数据, 请先执行数据下载。")

    all_data = pd.concat(frames, ignore_index=True)
    all_data['trade_date'] = pd.to_datetime(all_data['trade_date'], format='%Y%m%d')
    return all_data


def load_stock_info() -> pd.DataFrame:
    """从 SQLite 加载股票信息."""
    stocks = get_active_stocks(exclude_st=True, exclude_bse=True)
    if not stocks:
        return pd.DataFrame()
    return pd.DataFrame(stocks)


def clean_pipeline(
    all_data: pd.DataFrame,
    stock_info: pd.DataFrame,
    trade_days: List[str]
) -> pd.DataFrame:
    """数据清洗管道: 依次执行过滤.

    Args:
        all_data: 原始日线数据
        stock_info: 股票信息 (已含 is_st 标记)
        trade_days: 有效交易日列表

    Returns:
        清洗后的 DataFrame
    """
    # ── 过滤器 1: ST / *ST 剔除 ──
    st_codes = stock_info[stock_info['is_st'] == 1]['ts_code'].tolist()
    if st_codes:
        all_data = all_data[~all_data['ts_code'].isin(st_codes)].copy()
        st_count = len(st_codes)
    else:
        st_count = 0

    # ── 过滤器 2: 停牌剔除 (当日 vol=0) ──
    latest_date = trade_days[-1]
    latest_data = all_data[all_data['trade_date'] == latest_date]
    suspended_codes = latest_data[latest_data['vol'].fillna(0) == 0]['ts_code'].tolist()
    if suspended_codes:
        all_data = all_data[~all_data['ts_code'].isin(suspended_codes)].copy()

    # ── 过滤器 3: 北交所剔除 ──
    bse_codes = stock_info[stock_info['market'] == '北交所']['ts_code'].tolist()
    if bse_codes:
        all_data = all_data[~all_data['ts_code'].isin(bse_codes)].copy()

    # ── 过滤器 4: 新股/次新股过滤 (上市不足阈值) ──
    stock_info['list_date_dt'] = pd.to_datetime(stock_info['list_date'], format='%Y%m%d', errors='coerce')
    latest_dt = datetime.now()
    stock_info['days_since_listing'] = (latest_dt - stock_info['list_date_dt']).dt.days

    # 标记哪些股票不参与哪些计算 (与数据本身分离, 后续计算函数读取)
    # 此处仅剔除完全不可用的次新股 (少于 ATH_MIN_HISTORY_DAYS)
    too_new = stock_info[stock_info['days_since_listing'] < ATH_MIN_HISTORY_DAYS]['ts_code'].tolist()
    if too_new:
        all_data = all_data[~all_data['ts_code'].isin(too_new)].copy()

    # 记录清洗统计
    total_before = len(stock_info)
    total_after = all_data['ts_code'].nunique()
    filtered_count = total_before - total_after

    return all_data, {
        'total_before': total_before,
        'total_after': total_after,
        'st_removed': st_count,
        'suspended_removed': len(suspended_codes),
        'bse_removed': len(bse_codes),
        'too_new_removed': len(too_new),
        'filtered_total': filtered_count,
    }


def get_trade_days(pro, n: int = 250) -> List[str]:
    """获取最近 n 个有效交易日 (YYYYMMDD).

    使用 450 个自然日的回看窗口来保证覆盖约 250 个交易日。
    若可获取的交易日不足 n, 以实际数量为准 (至少需满足 MA200 计算)。
    """
    end = datetime.now().strftime('%Y%m%d')
    start = (datetime.now() - timedelta(days=450)).strftime('%Y%m%d')
    cal = pro.trade_cal(start_date=start, end_date=end)
    if cal is None or cal.empty:
        raise RuntimeError("无法获取交易日历, 请检查 TuShare 连接和 Token。")
    days = sorted(cal[cal['is_open'] == 1]['cal_date'].tolist())
    if len(days) < 200:
        raise RuntimeError(f"交易日数量严重不足 (需要至少 200, 仅有 {len(days)})。")
    # 取最后 n 个交易日, 若 n 超过实际数量则全部返回
    return days[-min(n, len(days)):]
