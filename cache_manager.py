"""利弗莫尔趋势捕捉器 — 缓存管理

物理触发机制:
  [全市场扫描] → FLUSH (清空) → COMPUTE (MA 排列) → RE-WRITE
  [趋势捕捉]   → 读取 MA 多头缓存 → COMPUTE (ATH/量比) → UPDATE → 入池
"""
import uuid
from datetime import datetime
from typing import Optional, List

from database import (
    flush_scan_cache, rewrite_scan_cache, update_scan_cache_trend,
    get_cache_status, create_scan_log, complete_scan_log, fail_scan_log,
    add_to_watch_pool, query_watch_pool,
)
from config import CACHE_TTL_HOURS


def generate_scan_id() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S_') + uuid.uuid4().hex[:8]


# ── 阶段一: FLUSH + RE-WRITE (MA 排列) ──

def flush_and_rewrite(results: List[dict], scan_id: str):
    """FLUSH + RE-WRITE: 清空旧缓存 → 写入全市场 MA 排列结果."""
    for r in results:
        r['scan_id'] = scan_id

    flush_scan_cache()

    batch_size = 500
    for i in range(0, len(results), batch_size):
        rewrite_scan_cache(results[i:i + batch_size])

    return len(results)


# ── 阶段二: 更新趋势数据 (ATH/量比/信号) ──

def update_trend_results(results: List[dict], scan_id: str):
    """将趋势捕捉结果更新到 scan_cache (不覆盖 MA 数据)."""
    for r in results:
        r['scan_id'] = scan_id

    batch_size = 500
    for i in range(0, len(results), batch_size):
        update_scan_cache_trend(results[i:i + batch_size])

    return len(results)


# ── 缓存状态 ──

def is_cache_valid() -> bool:
    """检查缓存是否有效 (有数据且未过期)."""
    status = get_cache_status()
    if status.get('total', 0) == 0:
        return False
    last_scan = status.get('last_scan')
    if last_scan is None:
        return False
    try:
        # SQLite CURRENT_TIMESTAMP 返回 "2024-01-15 10:30:00"
        # Python 3.9 fromisoformat 不支持空格分隔符, 需替换为 T
        ts_str = str(last_scan).replace(' ', 'T')
        last_time = datetime.fromisoformat(ts_str)
        hours_since = (datetime.now() - last_time).total_seconds() / 3600
        return hours_since < CACHE_TTL_HOURS
    except Exception:
        return False


def get_cache_summary() -> dict:
    status = get_cache_status()
    return {
        'total': status.get('total', 0),
        'ma_aligned': status.get('ma_aligned', 0),
        'ath_break': status.get('ath_break', 0),
        'volume_surge': status.get('volume_surge', 0),
        'livermore': status.get('livermore', 0),
        'last_scan': status.get('last_scan', ''),
    }


# ── 扫描日志 ──

def start_scan_log(scan_id: str, scan_type: str = 'FULL_SCAN') -> str:
    create_scan_log(scan_id, scan_type, datetime.now().isoformat())
    return scan_id


def finish_scan_log(scan_id: str, stats: dict):
    complete_scan_log(scan_id, stats)


def fail_scan(scan_id: str, error: str):
    fail_scan_log(scan_id, error)


# ── 趋势捕捉 → 重点关注池 ──

def trend_capture(
    scan_id: str,
    trend_records: List[dict],
    max_pool_size: int = 50
) -> List[dict]:
    """从趋势捕捉结果中筛选利弗莫尔信号标的, 写入 watch_pool.

    Args:
        scan_id: 扫描批次
        trend_records: 阶段二计算结果
        max_pool_size: 关注池上限

    Returns:
        新入选的标的列表
    """
    candidates = [r for r in trend_records if r.get('is_livermore') == 1]

    if not candidates:
        return []

    active_watch = query_watch_pool(status='ACTIVE')
    available_slots = max_pool_size - len(active_watch)
    if available_slots <= 0:
        return []

    candidates.sort(key=lambda x: x.get('vol_ratio', 0), reverse=True)
    to_add = candidates[:available_slots]

    added = []
    for c in to_add:
        entry_price = c.get('close_adj', 0)
        record = {
            'ts_code': c.get('ts_code', ''),
            'name': c.get('name', ''),
            'industry': c.get('industry', ''),
            'entry_reason': 'ATH_BREAK',
            'entry_price': entry_price,
            'entry_date': datetime.now().strftime('%Y%m%d'),
            'current_price': entry_price,
            'stop_loss_price': round(entry_price * 0.93, 2),
            'take_profit_price': round(entry_price * 1.20, 2),
            'entry_scan_id': scan_id,
        }
        if add_to_watch_pool(record):
            added.append(c)

    return added
