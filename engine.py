"""利弗莫尔趋势捕捉器 — 核心算法引擎

两阶段设计:
  阶段一 (全市场扫描):  MA 五层均线排列判定 → scan_cache
  阶段二 (趋势捕捉):    ATH + 成交量 + 利弗莫尔信号 → watch_pool

设计原则:
  - 纯批量向量化, 无逐股循环
  - 所有价格使用前复权
  - 线程安全: 每块数据独立, 无共享状态
"""
import gc
import numpy as np
import pandas as pd
from typing import Optional, Callable, List

from config import MA_PERIODS, MA_MIN_PERIODS, VOLUME_SURGE_RATIO, N_WORKERS, CHUNK_SIZE


# ═══════════════════════════════════════════════════════════
# 阶段一: MA 排列计算
# ═══════════════════════════════════════════════════════════

def compute_ma_alignment(data: pd.DataFrame) -> pd.DataFrame:
    """向量化计算五层均线并判定排列状态.

    Args:
        data: 含 ts_code, trade_date, close_adj 的 DataFrame

    Returns:
        latest: 每只股票最新行的 MA 值 + 排列标签
    """
    data = data.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)

    # 批量计算五层均线
    for p in MA_PERIODS:
        min_p = min(p, MA_MIN_PERIODS)
        data[f'ma_{p}'] = data.groupby('ts_code')['close_adj'].transform(
            lambda x: x.rolling(p, min_periods=min_p).mean()
        )

    # 取最新行
    latest = data.groupby('ts_code').last().reset_index()
    latest = latest[latest['ma_10'].notna()].copy()

    # 向量化排列判定
    is_multi_head = (
        (latest['ma_10'] > latest['ma_20']) &
        (latest['ma_20'] > latest['ma_50']) &
        (latest['ma_50'] > latest['ma_120']) &
        (latest['ma_120'] > latest['ma_200'])
    )
    is_short_bull = (
        (latest['ma_10'] > latest['ma_20']) &
        (latest['ma_20'] > latest['ma_50'])
    )
    is_bear = (
        (latest['ma_10'] < latest['ma_20']) &
        (latest['ma_20'] < latest['ma_50']) &
        (latest['ma_50'] < latest['ma_120']) &
        (latest['ma_120'] < latest['ma_200'])
    )
    is_weak = (
        (latest['ma_10'] < latest['ma_50']) |
        (latest['ma_20'] < latest['ma_50'])
    )

    conditions = [is_multi_head, is_short_bull, is_bear, is_weak]
    choices = ['MULTI_HEAD', 'SHORT_BULL', 'BEAR', 'WEAK']
    latest['ma_alignment'] = np.select(conditions, choices, default='NEUTRAL')

    return latest


def _nan(val, default=None):
    """将 pandas NaN 转为 None, 保持其他值不变."""
    return default if isinstance(val, float) and np.isnan(val) else val


def prepare_ma_results(latest: pd.DataFrame, scan_id: str) -> List[dict]:
    """阶段一: 仅打包 MA 相关字段到 scan_cache."""
    records = []
    for _, row in latest.iterrows():
        records.append({
            'ts_code': _nan(row.get('ts_code'), ''),
            'name': _nan(row.get('name'), ''),
            'industry': _nan(row.get('industry'), ''),
            'close': _nan(row.get('close')),
            'close_adj': _nan(row.get('close_adj')),
            'ma_10': _nan(row.get('ma_10')),
            'ma_20': _nan(row.get('ma_20')),
            'ma_50': _nan(row.get('ma_50')),
            'ma_120': _nan(row.get('ma_120')),
            'ma_200': _nan(row.get('ma_200')),
            'ma_alignment': _nan(row.get('ma_alignment'), 'NEUTRAL'),
            'scan_id': scan_id,
        })
    return records


def parallel_ma_scan(
    all_data: pd.DataFrame,
    progress_callback: Optional[Callable] = None,
    n_workers: int = N_WORKERS,
    chunk_size: int = CHUNK_SIZE,
) -> List[dict]:
    """全量一次性 MA 扫描 (阶段一).

    向量化计算全市场 MA 排列, 不分块, 避免逐块迭代和潜在卡顿.
    """
    if progress_callback:
        progress_callback(0, 1)

    ts_codes_before = all_data['ts_code'].nunique()
    latest = compute_ma_alignment(all_data)

    if progress_callback:
        progress_callback(1, 1)

    results = prepare_ma_results(latest, '')
    print(f"[MA_SCAN] {ts_codes_before} 只 → {len(results)} 只有效")
    return results


# ═══════════════════════════════════════════════════════════
# 阶段二: ATH + 成交量 + 利弗莫尔信号
# ═══════════════════════════════════════════════════════════

def compute_ath(data: pd.DataFrame) -> pd.DataFrame:
    """计算每只股票的前复权历史最高价 (ATH)."""
    if 'adj_high' not in data.columns:
        data['adj_high'] = data['high'] * data.get('adj_factor', 1.0)

    data['ath_price'] = data.groupby('ts_code')['adj_high'].cummax()

    # ATH 发生日期
    data['is_ath_day'] = data.groupby('ts_code')['adj_high'].transform(
        lambda x: x == x.cummax()
    ).astype(int)
    ath_dates = data[data['is_ath_day'] == 1].groupby('ts_code').last()
    ath_date_dict = ath_dates.to_dict()['trade_date'] if not ath_dates.empty else {}

    return data, ath_date_dict


def compute_volume_surge(data: pd.DataFrame) -> pd.DataFrame:
    """计算量比并标记放量 (1.5x)."""
    vol_ma_5 = data.groupby('ts_code')['vol'].transform(
        lambda x: x.rolling(5, min_periods=5).mean().shift(1)
    )
    data['vol_ma_5'] = vol_ma_5.fillna(0)
    data['vol_ratio'] = np.where(
        data['vol_ma_5'] > 0, data['vol'] / data['vol_ma_5'], 0.0
    )
    data['is_volume_surge'] = (data['vol_ratio'] >= VOLUME_SURGE_RATIO).astype(int)
    return data


def compute_livermore_signal(latest: pd.DataFrame) -> pd.DataFrame:
    """利弗莫尔关键点信号 = ATH突破 AND 放量确认 (MA已由阶段一保证)."""
    latest['is_ath_break'] = (latest['close_adj'] >= latest['ath_price']).astype(int)
    latest['is_livermore'] = (
        (latest['is_ath_break'] == 1) & (latest['is_volume_surge'] == 1)
    ).astype(int)
    return latest


def compute_trend_metrics(data: pd.DataFrame, ma_aligned_codes: List[str]) -> pd.DataFrame:
    """对 MA 多头排列标的计算 ATH + 量比 + 利弗莫尔信号.

    Args:
        data: 全量日线数据
        ma_aligned_codes: 阶段一筛选出的多头排列 ts_code 列表

    Returns:
        latest: 这些股票的最新行, 含 ATH/量比/信号
    """
    if 'adj_factor' in data.columns:
        data['close_adj'] = data['close'] * data['adj_factor']
        data['adj_high'] = data['high'] * data['adj_factor']
    else:
        data['close_adj'] = data['close']
        data['adj_high'] = data['high']

    # 只计算 MA 多头排列的股票
    subset = data[data['ts_code'].isin(ma_aligned_codes)].copy()
    if subset.empty:
        return pd.DataFrame()

    # ATH
    data_with_ath, ath_date_dict = compute_ath(subset)

    # 成交量
    data_with_vol = compute_volume_surge(data_with_ath)

    # 取最新行
    latest = data_with_vol.groupby('ts_code').last().reset_index()

    # 补齐缺失值
    for c in ['vol_ma_5', 'vol_ratio']:
        if c in latest.columns:
            latest[c] = latest[c].fillna(0.0)

    # 合并 ATH 信息
    last_idx = data_with_ath.groupby('ts_code').tail(1)
    ath_lookup = last_idx.set_index('ts_code')['ath_price']
    latest['ath_price'] = latest['ts_code'].map(ath_lookup)
    latest['ath_date'] = latest['ts_code'].map(ath_date_dict)

    # 利弗莫尔信号
    latest = compute_livermore_signal(latest)

    return latest


def prepare_trend_results(latest: pd.DataFrame, scan_id: str) -> List[dict]:
    """阶段二: 打包 ATH/量比/信号 + 名称行业, 用于更新 scan_cache 和入池."""
    records = []
    for _, row in latest.iterrows():
        ath_date = row.get('ath_date')
        records.append({
            'ts_code': _nan(row.get('ts_code'), ''),
            'name': _nan(row.get('name'), ''),
            'industry': _nan(row.get('industry'), ''),
            'close': _nan(row.get('close')),
            'close_adj': _nan(row.get('close_adj')),
            'ath_price': _nan(row.get('ath_price')),
            'ath_date': str(_nan(ath_date, '')) if pd.notna(ath_date) else None,
            'is_ath_break': int(row.get('is_ath_break', 0)),
            'vol': row.get('vol', 0),
            'vol_ma_5': row.get('vol_ma_5', 0),
            'vol_ratio': row.get('vol_ratio', 0),
            'is_volume_surge': int(row.get('is_volume_surge', 0)),
            'is_livermore': int(row.get('is_livermore', 0)),
            'scan_id': scan_id,
        })
    return records


def parallel_trend_capture(
    all_data: pd.DataFrame,
    ma_aligned_codes: List[str],
    progress_callback: Optional[Callable] = None,
    n_workers: int = N_WORKERS,
    chunk_size: int = CHUNK_SIZE,
) -> List[dict]:
    """全量一次性趋势捕捉 (阶段二).

    只对 MA 多头排列的标的计算 ATH + 量比 + 利弗莫尔信号, 不分块.
    """
    if not ma_aligned_codes:
        return []

    if progress_callback:
        progress_callback(0, 1)

    latest = compute_trend_metrics(all_data, ma_aligned_codes)
    results = prepare_trend_results(latest, '') if not latest.empty else []

    if progress_callback:
        progress_callback(1, 1)

    print(f"[TREND_CAPTURE] {len(ma_aligned_codes)} 只 → {len(results)} 只")
    return results
