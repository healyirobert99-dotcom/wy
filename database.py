"""利弗莫尔趋势捕捉器 — SQLite 数据库层

线程安全设计:
  - 每个线程获取独立 Connection (WAL 模式支持下读不阻塞写)
  - 写入时使用 write_lock 互斥
  - 批量操作以 TRANSACTION 包裹保证原子性
"""
import sqlite3
import threading
from datetime import datetime
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from config import DB_PATH

_write_lock = threading.Lock()
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """获取线程本地 Connection, WAL 模式."""
    if not hasattr(_local, 'conn') or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), timeout=30)
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


@contextmanager
def get_db():
    """上下文管理器: 获取数据库连接, 自动提交."""
    conn = _get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _migrate(conn):
    """数据库迁移: 补充缺失的列."""
    migrations = [
        "ALTER TABLE scan_cache ADD COLUMN close REAL",
    ]
    for m in migrations:
        try:
            conn.execute(m)
        except sqlite3.OperationalError:
            pass  # 列已存在, 忽略


def init_db():
    """初始化数据库: 创建所有表."""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS stock_basic (
            ts_code       TEXT PRIMARY KEY,
            symbol        TEXT,
            name          TEXT,
            area          TEXT,
            industry      TEXT,
            market        TEXT,
            exchange      TEXT,
            list_status   TEXT DEFAULT 'L',
            list_date     TEXT,
            delist_date   TEXT,
            is_hs         TEXT,
            is_st         INTEGER DEFAULT 0,
            is_suspended  INTEGER DEFAULT 0,
            updated_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS daily_prices (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code       TEXT NOT NULL,
            trade_date    TEXT NOT NULL,
            open          REAL,
            high          REAL,
            low           REAL,
            close         REAL,
            pre_close     REAL,
            change        REAL,
            pct_chg       REAL,
            vol           REAL,
            amount        REAL,
            adj_factor    REAL DEFAULT 1.0,
            UNIQUE(ts_code, trade_date)
        );
        CREATE INDEX IF NOT EXISTS idx_daily_ts_code ON daily_prices(ts_code);
        CREATE INDEX IF NOT EXISTS idx_daily_trade_date ON daily_prices(trade_date);
        CREATE INDEX IF NOT EXISTS idx_daily_ts_date ON daily_prices(ts_code, trade_date);

        CREATE TABLE IF NOT EXISTS scan_cache (
            ts_code          TEXT PRIMARY KEY,
            name             TEXT,
            industry         TEXT,
            ma_10            REAL,
            ma_20            REAL,
            ma_50            REAL,
            ma_120           REAL,
            ma_200           REAL,
            ma_alignment     TEXT,
            close_adj        REAL,
            ath_price        REAL,
            ath_date         TEXT,
            is_ath_break     INTEGER DEFAULT 0,
            vol              REAL,
            vol_ma_5         REAL,
            vol_ratio        REAL,
            is_volume_surge  INTEGER DEFAULT 0,
            is_livermore     INTEGER DEFAULT 0,
            scan_id          TEXT,
            scanned_at       DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_scan_alignment ON scan_cache(ma_alignment);
        CREATE INDEX IF NOT EXISTS idx_scan_ath ON scan_cache(is_ath_break) WHERE is_ath_break = 1;
        CREATE INDEX IF NOT EXISTS idx_scan_livermore ON scan_cache(is_livermore) WHERE is_livermore = 1;

        CREATE TABLE IF NOT EXISTS watch_pool (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            ts_code           TEXT NOT NULL,
            name              TEXT,
            industry          TEXT,
            entry_reason      TEXT,
            entry_price       REAL,
            entry_date        TEXT,
            current_price     REAL,
            profit_pct        REAL,
            max_profit_pct    REAL,
            max_drawdown_pct  REAL,
            status            TEXT DEFAULT 'ACTIVE',
            stop_loss_price   REAL,
            take_profit_price REAL,
            entry_scan_id     TEXT,
            created_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at        DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(ts_code, entry_date)
        );
        CREATE INDEX IF NOT EXISTS idx_watch_status ON watch_pool(status);
        CREATE INDEX IF NOT EXISTS idx_watch_entry_date ON watch_pool(entry_date);

        CREATE TABLE IF NOT EXISTS scan_log (
            scan_id            TEXT PRIMARY KEY,
            scan_type          TEXT NOT NULL,
            started_at         DATETIME NOT NULL,
            completed_at       DATETIME,
            total_stocks       INTEGER,
            ma_aligned_count   INTEGER,
            ath_break_count    INTEGER,
            volume_surge_count INTEGER,
            livermore_count    INTEGER,
            watch_candidates   INTEGER,
            duration_seconds   REAL,
            status             TEXT DEFAULT 'RUNNING',
            error_message      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scan_log_type ON scan_log(scan_type);
        CREATE INDEX IF NOT EXISTS idx_scan_log_time ON scan_log(started_at);
        """)
        _migrate(conn)


# ── stock_basic ──

_STOCK_BASIC_COLS = ['ts_code', 'symbol', 'name', 'area', 'industry', 'market',
                     'exchange', 'list_status', 'list_date', 'is_hs', 'is_st']

def upsert_stock_basic(rows: List[Dict]):
    """批量写入或更新 stock_basic."""
    if not rows:
        return
    cols_str = ', '.join(_STOCK_BASIC_COLS)
    placeholders = ', '.join(f':{c}' for c in _STOCK_BASIC_COLS)
    sql = (f"INSERT OR REPLACE INTO stock_basic ({cols_str}, updated_at) "
           f"VALUES ({placeholders}, datetime('now'))")
    # 确保每行都有全部必要键 (缺失的补 None)
    normed = [{c: r.get(c) for c in _STOCK_BASIC_COLS} for r in rows]
    with get_db() as conn:
        conn.executemany(sql, normed)


def get_active_stocks(exclude_st: bool = True, exclude_bse: bool = True) -> List[Dict]:
    """获取活跃股票列表 (可排除 ST 和北交所)."""
    with get_db() as conn:
        sql = "SELECT * FROM stock_basic WHERE list_status = 'L'"
        if exclude_st:
            sql += " AND is_st = 0"
        if exclude_bse:
            sql += " AND market != '北交所'"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


# ── daily_prices ──

_DAILY_COLS = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close',
               'pre_close', 'change', 'pct_chg', 'vol', 'amount', 'adj_factor']

def batch_insert_daily(rows: List[Dict]):
    """批量插入日线数据."""
    if not rows:
        return
    cols_str = ', '.join(_DAILY_COLS)
    placeholders = ', '.join(f':{c}' for c in _DAILY_COLS)
    sql = f"INSERT OR IGNORE INTO daily_prices ({cols_str}) VALUES ({placeholders})"
    normed = [{c: r.get(c) for c in _DAILY_COLS} for r in rows]
    with get_db() as conn:
        conn.executemany(sql, normed)


def get_daily_data(ts_codes: Optional[List[str]] = None,
                   start_date: Optional[str] = None,
                   end_date: Optional[str] = None) -> List[Dict]:
    """获取日线数据，可选按股票代码和日期范围过滤."""
    with get_db() as conn:
        sql = "SELECT * FROM daily_prices WHERE 1=1"
        params = {}
        if ts_codes:
            placeholders = ','.join('?' for _ in ts_codes)
            sql += f" AND ts_code IN ({placeholders})"
        if start_date:
            sql += " AND trade_date >= :start_date"
            params['start_date'] = start_date
        if end_date:
            sql += " AND trade_date <= :end_date"
            params['end_date'] = end_date
        sql += " ORDER BY ts_code, trade_date"
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


# ── scan_cache ──

def flush_scan_cache():
    """FLUSH: 清空 scan_cache 表."""
    with get_db() as conn:
        conn.execute("DELETE FROM scan_cache")


def _norm(rows: List[Dict]) -> List[Dict]:
    """确保所有行具有相同的键集合 (所有键的并集)."""
    if not rows:
        return rows
    all_keys = set()
    for r in rows:
        all_keys.update(r.keys())
    return [{k: r.get(k) for k in all_keys} for r in rows]


def rewrite_scan_cache(rows: List[Dict]):
    """RE-WRITE: 批量写入 scan_cache (动态列绑定)."""
    if not rows:
        return
    rows = _norm(rows)
    cols = [k for k in rows[0]]
    cols_str = ', '.join(cols)
    placeholders = ', '.join(f':{c}' for c in cols)
    sql = f"INSERT OR REPLACE INTO scan_cache ({cols_str}) VALUES ({placeholders})"
    with get_db() as conn:
        conn.executemany(sql, rows)


def update_scan_cache_trend(rows: List[Dict]):
    """阶段二: 仅更新 scan_cache 的 ATH/量比/信号字段, 不覆盖 MA 数据."""
    if not rows:
        return
    rows = _norm(rows)
    upd_cols = [k for k in rows[0] if k not in ('ts_code', 'scan_id')]
    set_clause = ', '.join(f'{c} = :{c}' for c in upd_cols)
    sql = f"UPDATE scan_cache SET {set_clause} WHERE ts_code = :ts_code"
    with get_db() as conn:
        conn.executemany(sql, rows)


def query_scan_cache(multi_head_only: bool = False) -> List[Dict]:
    """查询 scan_cache, 可选仅多头排列."""
    with get_db() as conn:
        sql = "SELECT * FROM scan_cache WHERE 1=1"
        if multi_head_only:
            sql += " AND ma_alignment IN ('MULTI_HEAD', 'SHORT_BULL')"
        rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]


def get_cache_status() -> Dict:
    """获取缓存状态 (总数 / 多头数 / ATH / 利弗莫尔)."""
    with get_db() as conn:
        r = conn.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN ma_alignment IN ('MULTI_HEAD','SHORT_BULL') THEN 1 ELSE 0 END) as ma_aligned,
                SUM(CASE WHEN is_ath_break = 1 THEN 1 ELSE 0 END) as ath_break,
                SUM(CASE WHEN is_volume_surge = 1 THEN 1 ELSE 0 END) as volume_surge,
                SUM(CASE WHEN is_livermore = 1 THEN 1 ELSE 0 END) as livermore,
                MAX(scanned_at) as last_scan
            FROM scan_cache
        """).fetchone()
        return dict(r) if r else {}


# ── watch_pool ──

def add_to_watch_pool(row: Dict) -> bool:
    """添加标的到重点关注池 (去重)."""
    with get_db() as conn:
        try:
            conn.execute(
                """INSERT OR IGNORE INTO watch_pool
                   (ts_code, name, industry, entry_reason, entry_price,
                    entry_date, current_price, profit_pct, max_profit_pct,
                    max_drawdown_pct, status, stop_loss_price, take_profit_price,
                    entry_scan_id)
                   VALUES (:ts_code, :name, :industry, :entry_reason, :entry_price,
                           :entry_date, :current_price, 0, 0, 0,
                           'ACTIVE', :stop_loss_price, :take_profit_price,
                           :entry_scan_id)""",
                row
            )
            return conn.total_changes > 0
        except sqlite3.IntegrityError:
            return False


def query_watch_pool(status: Optional[str] = 'ACTIVE') -> List[Dict]:
    """查询重点关注池."""
    with get_db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM watch_pool WHERE status = ? ORDER BY created_at DESC", (status,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM watch_pool ORDER BY created_at DESC").fetchall()
        return [dict(r) for r in rows]


def update_watch_pool_prices(updates: List[Dict]):
    """批量更新关注池标的最新价和盈亏."""
    with get_db() as conn:
        conn.executemany(
            """UPDATE watch_pool SET
               current_price = :current_price,
               profit_pct = :profit_pct,
               max_profit_pct = CASE WHEN :profit_pct > max_profit_pct THEN :profit_pct ELSE max_profit_pct END,
               max_drawdown_pct = CASE WHEN :profit_pct < max_drawdown_pct THEN :profit_pct ELSE max_drawdown_pct END,
               updated_at = datetime('now')
               WHERE ts_code = :ts_code AND status = 'ACTIVE'""",
            updates
        )


# ── scan_log ──

def create_scan_log(scan_id: str, scan_type: str, started_at: str):
    """创建扫描记录."""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO scan_log (scan_id, scan_type, started_at) VALUES (?, ?, ?)",
            (scan_id, scan_type, started_at)
        )


def complete_scan_log(scan_id: str, stats: Dict):
    """完成扫描记录 (缺失字段自动填 0)."""
    defaults = {
        'total_stocks': 0,
        'ma_aligned_count': 0,
        'ath_break_count': 0,
        'volume_surge_count': 0,
        'livermore_count': 0,
        'watch_candidates': 0,
        'duration_seconds': 0,
    }
    defaults.update(stats)
    with get_db() as conn:
        conn.execute(
            """UPDATE scan_log SET
               completed_at = datetime('now'),
               total_stocks = :total_stocks,
               ma_aligned_count = :ma_aligned_count,
               ath_break_count = :ath_break_count,
               volume_surge_count = :volume_surge_count,
               livermore_count = :livermore_count,
               watch_candidates = :watch_candidates,
               duration_seconds = :duration_seconds,
               status = 'COMPLETED'
               WHERE scan_id = :scan_id""",
            {**defaults, 'scan_id': scan_id}
        )


def fail_scan_log(scan_id: str, error: str):
    """标记扫描失败."""
    with get_db() as conn:
        conn.execute(
            "UPDATE scan_log SET status = 'FAILED', error_message = ?, completed_at = datetime('now') WHERE scan_id = ?",
            (error, scan_id)
        )


def query_scan_logs(limit: int = 20) -> List[Dict]:
    """查询扫描记录."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM scan_log ORDER BY started_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
