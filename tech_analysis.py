"""技术面深度分析模块

集成两个 Skill 的能力:
  - eastmoney-tech-analysis: KDJ/MACD/BOLL/MA 指标 + 资金流向
  - quant-trading-assistant: 实时行情 + 龙头战法 + 风险检查 + 市场情绪 + 综合建议

输出统一的技术面分析报告，供 Streamlit 展示。
"""
import os
import sys
from typing import Dict, Any, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BASE_DIR, 'skills', 'eastmoney_tech'))
sys.path.insert(0, os.path.join(BASE_DIR, 'skills', 'quant_trading'))

from eastmoney_spider import EastmoneySpider
from indicators import calculate_kdj, calculate_macd, calculate_boll, calculate_ma, get_latest_signals
from quant_trading_assistant import (
    get_stock_quote,
    get_technical_indicators as qt_get_tech,
    check_dragon_signals,
    risk_check,
    get_market_sentiment,
)


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def ts_code_to_symbol(ts_code: str) -> str:
    """000001.SZ -> sz000001"""
    if '.' in ts_code:
        code, suffix = ts_code.upper().split('.')
        return suffix.lower() + code
    return ts_code.lower()


def ts_code_to_pure(ts_code: str) -> str:
    """000001.SZ -> 000001"""
    return ts_code.split('.')[0]


def safe_fmt(value, fmt="{:.2f}", default="--"):
    try:
        if value is None:
            return default
        return fmt.format(value)
    except Exception:
        return default


# ═══════════════════════════════════════════════════════════
# 核心分析函数
# ═══════════════════════════════════════════════════════════

def analyze_stock_tech(ts_code: str, name: str = "") -> Dict[str, Any]:
    """对单只股票进行全面的技术面分析，整合两个 Skill 的数据。

    Returns:
        dict: 包含 quote / indicators / signals / dragon / risk / sentiment / rating
    """
    symbol = ts_code_to_symbol(ts_code)
    pure_code = ts_code_to_pure(ts_code)

    result = {
        "ts_code": ts_code,
        "name": name,
        "quote": None,
        "em_indicators": {},
        "em_signals": {},
        "qt_tech": {},
        "dragon": {},
        "risk": {},
        "sentiment": {},
        "rating": {},
        "error": None,
    }

    # ── 1. K线 + 指标（东方财富优先，国外服务器回退到 Tushare）──
    try:
        spider = EastmoneySpider()
        df = spider.get_stock_kline(pure_code, days=120)

        # 回退：Streamlit Cloud 等国外服务器常被东方财富拒绝 IP
        if df.empty or len(df) < 30:
            import tushare as ts
            from datetime import datetime, timedelta
            from config import TUSHARE_TOKEN
            if not TUSHARE_TOKEN:
                result["error"] = "TUSHARE_TOKEN 未配置，请在 Streamlit Secrets 中设置"
            else:
                try:
                    pro = ts.pro_api(TUSHARE_TOKEN)
                    end_date = datetime.now().strftime('%Y%m%d')
                    start_date = (datetime.now() - timedelta(days=180)).strftime('%Y%m%d')
                    df_ts = pro.daily(ts_code=ts_code, start_date=start_date, end_date=end_date)
                    if df_ts is not None and not df_ts.empty and len(df_ts) >= 30:
                        df_ts = df_ts.sort_values('trade_date').reset_index(drop=True)
                        df_ts = df_ts.rename(columns={'trade_date': 'date', 'vol': 'volume'})
                        if 'amount' in df_ts.columns:
                            df_ts['amount'] = df_ts['amount'] * 1000
                        df = df_ts
                    else:
                        result["error"] = f"Tushare 返回数据为空或不完整({ts_code})，请检查 token 积分权限"
                except Exception as e:
                    result["error"] = f"Tushare 数据获取失败: {e}"

        if not df.empty and len(df) >= 30:
            kdj = calculate_kdj(df)
            macd = calculate_macd(df)
            boll = calculate_boll(df)
            ma = calculate_ma(df)
            em_signals = get_latest_signals(df)

            result["em_indicators"] = {
                "kdj": {
                    "k": round(kdj["k"][-1], 2) if kdj["k"] else None,
                    "d": round(kdj["d"][-1], 2) if kdj["d"] else None,
                    "j": round(kdj["j"][-1], 2) if kdj["j"] else None,
                },
                "macd": {
                    "dif": round(macd["dif"][-1], 4) if macd["dif"] else None,
                    "dea": round(macd["dea"][-1], 4) if macd["dea"] else None,
                    "macd": round(macd["macd"][-1], 4) if macd["macd"] else None,
                },
                "boll": {
                    "upper": round(boll["upper"][-1], 2) if boll["upper"] else None,
                    "middle": round(boll["middle"][-1], 2) if boll["middle"] else None,
                    "lower": round(boll["lower"][-1], 2) if boll["lower"] else None,
                },
                "ma": {
                    "ma5": round(ma["ma5"][-1], 2) if "ma5" in ma else None,
                    "ma10": round(ma["ma10"][-1], 2) if "ma10" in ma else None,
                    "ma20": round(ma["ma20"][-1], 2) if "ma20" in ma else None,
                    "ma60": round(ma["ma60"][-1], 2) if "ma60" in ma else None,
                },
            }
            result["em_signals"] = em_signals
            result["kline_df"] = df
        else:
            result["kline_df"] = df
    except Exception as e:
        result["error"] = f"东方财富数据获取失败: {e}"

    # ── 2. 量化交易助手 ──
    try:
        quote = get_stock_quote(symbol)
        if quote and "error" not in quote:
            result["quote"] = quote
            result["name"] = name or quote.get("name", "")
    except Exception as e:
        result["error"] = f"{result['error'] or ''}; 实时行情获取失败: {e}"

    try:
        result["qt_tech"] = qt_get_tech(symbol) or {}
    except Exception:
        result["qt_tech"] = {}

    try:
        result["dragon"] = check_dragon_signals(symbol) or {}
    except Exception:
        result["dragon"] = {}

    try:
        result["risk"] = risk_check(symbol) or {}
    except Exception:
        result["risk"] = {}

    try:
        result["sentiment"] = get_market_sentiment() or {}
    except Exception:
        result["sentiment"] = {}

    # ── 3. 综合评级 ──
    result["rating"] = _generate_comprehensive_rating(result)
    return result


# ═══════════════════════════════════════════════════════════
# 综合评级逻辑
# ═══════════════════════════════════════════════════════════

def _generate_comprehensive_rating(data: Dict) -> Dict[str, Any]:
    """基于多维度数据生成综合评级、交易建议和止损位。"""
    quote = data.get("quote") or {}
    em_ind = data.get("em_indicators", {})
    em_sig = data.get("em_signals", {})
    qt_tech = data.get("qt_tech", {})
    dragon = data.get("dragon", {})
    risk = data.get("risk", {})
    sentiment = data.get("sentiment", {})

    price = quote.get("price", 0)
    risk_score = risk.get("risk_score", 100)
    risk_level = risk.get("risk_level", "安全")
    sentiment_score = sentiment.get("score", 50)
    sentiment_phase = sentiment.get("phase", "未知")

    # 风险一票否决
    if risk_level == "高危" or risk_score < 40:
        return {
            "grade": "D",
            "grade_label": "强烈回避",
            "grade_color": "#ef4444",
            "reason": f"风险等级为高危: {risk.get('risks', ['未知风险'])[0]}",
            "action": "回避",
            "action_color": "#ef4444",
            "entry": None,
            "stop_loss": None,
            "position": "空仓",
        }

    score = 50  # 基准分
    reasons = []

    # KDJ 评分
    kdj_sig = em_sig.get("kdj", {})
    j_val = kdj_sig.get("j", 50)
    if isinstance(j_val, (int, float)):
        if j_val < 20:
            score += 10
            reasons.append("KDJ 超卖，存在反弹机会")
        elif j_val > 80:
            score -= 10
            reasons.append("KDJ 超买，注意回调风险")
        if kdj_sig.get("signal") == 1:
            score += 8
            reasons.append("KDJ 金叉")
        elif kdj_sig.get("signal") == -1:
            score -= 8
            reasons.append("KDJ 死叉")

    # MACD 评分
    macd_sig = em_sig.get("macd", {})
    if macd_sig.get("trend") == "多头":
        score += 8
        reasons.append("MACD 多头格局")
    elif macd_sig.get("trend") == "空头":
        score -= 8
        reasons.append("MACD 空头格局")
    if macd_sig.get("signal") == 1:
        score += 10
        reasons.append("MACD 金叉")
    elif macd_sig.get("signal") == -1:
        score -= 10
        reasons.append("MACD 死叉")

    # BOLL 评分
    boll_sig = em_sig.get("boll", "")
    if "下轨" in str(boll_sig):
        score += 5
        reasons.append("股价接近布林下轨，或有支撑")
    elif "上轨" in str(boll_sig):
        score -= 5
        reasons.append("股价接近布林上轨，或有压力")

    # 均线趋势评分
    ma_trend = qt_tech.get("ma_trend", "")
    if "多头" in str(ma_trend):
        score += 10
        reasons.append("均线多头排列")
    elif "空头" in str(ma_trend):
        score -= 10
        reasons.append("均线空头排列")

    # 量比评分
    vol_ratio = qt_tech.get("volume_ratio", 1)
    if vol_ratio > 2:
        score += 8
        reasons.append(f"显著放量(量比{vol_ratio})")
    elif vol_ratio > 1.5:
        score += 5
        reasons.append(f"温和放量(量比{vol_ratio})")
    elif vol_ratio < 0.7:
        score -= 5
        reasons.append("缩量，动能不足")

    # 龙头战法评分
    dragon_signals = dragon.get("signals", [])
    if dragon_signals:
        best = max(dragon_signals, key=lambda x: x.get("level", 0))
        lvl = best.get("level", 0)
        if lvl >= 3:
            score += 12
            reasons.append(f"龙头信号: {best['type']}")
        elif lvl >= 2:
            score += 8
            reasons.append(f"强势信号: {best['type']}")
        else:
            score += 4
            reasons.append(f"关注信号: {best['type']}")
    else:
        reasons.append("无明显龙头战法信号")

    # 市场情绪评分
    if sentiment_score >= 70:
        score += 5
        reasons.append(f"市场情绪高涨({sentiment_phase})")
    elif sentiment_score <= 35:
        score -= 8
        reasons.append(f"市场情绪低迷({sentiment_phase})")

    # 风险扣分
    if risk_level == "中等":
        score -= 5
        reasons.append("存在中等风险")

    # 涨跌幅辅助
    pct = quote.get("change_pct", 0)
    if isinstance(pct, (int, float)):
        if pct > 9.5:
            score += 3
            reasons.append("涨停，资金强烈看多")
        elif pct < -5:
            score -= 5
            reasons.append("当日大跌，注意风险")

    # 区间映射到评级
    score = max(0, min(100, score))
    if score >= 80:
        grade, label, color = "A", "强烈推荐", "#22c55e"
        action, action_color = "买入", "#22c55e"
        position = "重仓"
    elif score >= 65:
        grade, label, color = "B", "推荐关注", "#4ade80"
        action, action_color = "关注买入", "#4ade80"
        position = "中仓"
    elif score >= 50:
        grade, label, color = "C", "中性观望", "#f59e0b"
        action, action_color = "观望", "#f59e0b"
        position = "轻仓"
    elif score >= 35:
        grade, label, color = "D", "谨慎对待", "#f97316"
        action, action_color = "减仓", "#f97316"
        position = "减仓"
    else:
        grade, label, color = "E", "回避", "#ef4444"
        action, action_color = "卖出", "#ef4444"
        position = "空仓"

    # 止损位计算
    stop_loss = None
    if price and isinstance(price, (int, float)) and price > 0:
        if grade in ("A", "B"):
            stop_loss = round(price * 0.92, 2)
        elif grade == "C":
            stop_loss = round(price * 0.90, 2)
        else:
            stop_loss = round(price * 0.88, 2)

    # ═══════════════════════════════════════════════════════════
    # 多因子共振判断
    # ═══════════════════════════════════════════════════════════
    # 1. 四个技术指标同向看涨共振
    kdj_bull = kdj_sig.get("signal") == 1 or (isinstance(j_val, (int, float)) and j_val < 30)
    macd_bull = macd_sig.get("trend") == "多头" or macd_sig.get("signal") == 1
    boll_not_overbought = "上轨" not in str(em_sig.get("boll", ""))
    ma_bull = "多头" in str(qt_tech.get("ma_trend", ""))
    tech_resonance = kdj_bull and macd_bull and boll_not_overbought and ma_bull

    # 2. 龙头信号强
    dragon_strong = False
    if dragon_signals:
        best_lvl = max(s.get("level", 0) for s in dragon_signals)
        dragon_strong = best_lvl >= 2

    # 3. 风险安全
    risk_safe = risk_level == "安全" and risk_score >= 70

    # 4. 情绪启动/发酵/高潮
    sentiment_good = sentiment_phase in ("启动", "发酵", "高潮") or sentiment_score >= 55

    resonance = tech_resonance and dragon_strong and risk_safe and sentiment_good
    resonance_reasons = []
    if tech_resonance:
        resonance_reasons.append("技术指标共振")
    if dragon_strong:
        resonance_reasons.append("龙头信号强劲")
    if risk_safe:
        resonance_reasons.append("风险可控")
    if sentiment_good:
        resonance_reasons.append(f"情绪{sentiment_phase}")

    return {
        "score": score,
        "grade": grade,
        "grade_label": label,
        "grade_color": color,
        "reasons": reasons,
        "action": action,
        "action_color": action_color,
        "entry": price if price else None,
        "stop_loss": stop_loss,
        "position": position,
        "resonance": resonance,
        "resonance_reasons": resonance_reasons,
    }


# ═══════════════════════════════════════════════════════════
# HTML 报告生成
# ═══════════════════════════════════════════════════════════

def generate_analysis_html(data: Dict) -> str:
    """生成技术面分析报告的 HTML 卡片，供 Streamlit 渲染。"""
    if data.get("error") and not data.get("quote"):
        return f"""
        <div style="padding:1.5rem;background:rgba(239,68,68,0.08);border:1px solid rgba(239,68,68,0.2);
                    border-radius:14px;color:#f87171;">
            <div style="font-weight:700;margin-bottom:8px;">分析失败</div>
            <div style="font-size:0.85rem;">{data['error']}</div>
        </div>
        """

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
    change = quote.get("change", 0)
    change_pct = quote.get("change_pct", 0)
    pct_color = "#22c55e" if change >= 0 else "#ef4444"
    pct_sign = "+" if change >= 0 else ""

    def hex_to_rgba(hex_color: str, alpha: float) -> str:
        h = hex_color.lstrip('#')
        r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
        return f"rgba({r},{g},{b},{alpha})"

    # ── 评级徽章 ──
    grade = rating.get("grade", "C")
    grade_label = rating.get("grade_label", "中性观望")
    grade_color = rating.get("grade_color", "#f59e0b")
    score = rating.get("score", 50)
    action = rating.get("action", "观望")
    action_color = rating.get("action_color", "#f59e0b")
    stop_loss = rating.get("stop_loss")
    reasons = rating.get("reasons", [])

    # ── KDJ ──
    kdj = em_ind.get("kdj", {})
    kdj_html = f"""
    <div style="flex:1;min-width:140px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px;">
        <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:6px;">KDJ</div>
        <div style="display:flex;gap:12px;font-size:0.85rem;">
            <div>K <span style="color:#f8fafc;font-weight:600;">{safe_fmt(kdj.get('k'))}</span></div>
            <div>D <span style="color:#f8fafc;font-weight:600;">{safe_fmt(kdj.get('d'))}</span></div>
            <div>J <span style="color:#f8fafc;font-weight:600;">{safe_fmt(kdj.get('j'))}</span></div>
        </div>
        <div style="margin-top:6px;font-size:0.75rem;color:#94a3b8;">
            {em_sig.get('kdj', {}).get('status', '--')} | { '金叉' if em_sig.get('kdj', {}).get('signal') == 1 else ('死叉' if em_sig.get('kdj', {}).get('signal') == -1 else '无交叉') }
        </div>
    </div>
    """

    # ── MACD ──
    macd = em_ind.get("macd", {})
    macd_trend = em_sig.get("macd", {}).get("trend", "--")
    macd_cross = "金叉" if em_sig.get("macd", {}).get("signal") == 1 else ("死叉" if em_sig.get("macd", {}).get("signal") == -1 else "无交叉")
    macd_html = f"""
    <div style="flex:1;min-width:140px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px;">
        <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:6px;">MACD</div>
        <div style="display:flex;gap:12px;font-size:0.85rem;">
            <div>DIF <span style="color:#f8fafc;font-weight:600;">{safe_fmt(macd.get('dif'), '{:.4f}')}</span></div>
            <div>DEA <span style="color:#f8fafc;font-weight:600;">{safe_fmt(macd.get('dea'), '{:.4f}')}</span></div>
        </div>
        <div style="margin-top:6px;font-size:0.75rem;color:#94a3b8;">
            {macd_trend} | {macd_cross}
        </div>
    </div>
    """

    # ── BOLL ──
    boll = em_ind.get("boll", {})
    boll_pos = em_sig.get("boll", "--")
    boll_html = f"""
    <div style="flex:1;min-width:140px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px;">
        <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:6px;">布林带</div>
        <div style="display:flex;gap:12px;font-size:0.85rem;">
            <div>上 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(boll.get('upper'))}</span></div>
            <div>中 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(boll.get('middle'))}</span></div>
            <div>下 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(boll.get('lower'))}</span></div>
        </div>
        <div style="margin-top:6px;font-size:0.75rem;color:#94a3b8;">股价位置: {boll_pos}</div>
    </div>
    """

    # ── MA ──
    ma = em_ind.get("ma", {})
    ma_trend = qt_tech.get("ma_trend", "--")
    ma_html = f"""
    <div style="flex:1;min-width:140px;background:#0f172a;border:1px solid #1e293b;border-radius:10px;padding:12px;">
        <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:6px;">均线</div>
        <div style="display:flex;gap:10px;font-size:0.85rem;flex-wrap:wrap;">
            <div>MA5 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(ma.get('ma5'))}</span></div>
            <div>MA10 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(ma.get('ma10'))}</span></div>
            <div>MA20 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(ma.get('ma20'))}</span></div>
            <div>MA60 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(ma.get('ma60'))}</span></div>
        </div>
        <div style="margin-top:6px;font-size:0.75rem;color:#94a3b8;">趋势: {ma_trend}</div>
    </div>
    """

    # ── 龙头信号 ──
    dragon_signals = dragon.get("signals", [])
    if dragon_signals:
        dragon_items = ""
        for s in dragon_signals[:3]:
            lvl = s.get("level", 1)
            fire = "🔥" * lvl
            dragon_items += f"""
            <div style="display:flex;align-items:center;gap:8px;padding:8px 10px;background:#0f172a;border-radius:8px;margin-bottom:6px;">
                <span style="font-size:0.9rem;">{fire}</span>
                <div>
                    <div style="font-weight:600;font-size:0.8rem;color:#f8fafc;">{s.get('type', '')}</div>
                    <div style="font-size:0.7rem;color:#94a3b8;">{s.get('desc', '')}</div>
                </div>
                <div style="margin-left:auto;font-size:0.75rem;color:#f59e0b;font-weight:600;">{s.get('action', '')}</div>
            </div>
            """
        dragon_html = f"""
        <div style="margin-top:16px;">
            <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:8px;">龙头战法信号</div>
            {dragon_items}
        </div>
        """
    else:
        dragon_html = f"""
        <div style="margin-top:16px;">
            <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:8px;">龙头战法信号</div>
            <div style="padding:10px;background:#0f172a;border-radius:8px;color:#94a3b8;font-size:0.8rem;">无明显龙头战法信号</div>
        </div>
        """

    # ── 风险检查 ──
    risk_score = risk.get("risk_score", 100)
    risk_level = risk.get("risk_level", "安全")
    risk_color = "#22c55e" if risk_level == "安全" else ("#f59e0b" if risk_level == "中等" else "#ef4444")
    risk_items = ""
    for r in risk.get("risks", ["✅ 无明显风险"]):
        risk_items += f'<div style="font-size:0.8rem;color:#94a3b8;margin-bottom:4px;">{r}</div>'

    risk_html = f"""
    <div style="margin-top:16px;">
        <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:8px;">风险检查</div>
        <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
            <div style="font-size:1.4rem;font-weight:700;color:{risk_color};">{risk_score}</div>
            <div style="font-size:0.85rem;color:{risk_color};font-weight:600;">{risk_level}</div>
        </div>
        {risk_items}
    </div>
    """

    # ── 市场情绪 ──
    sentiment_phase = sentiment.get("phase", "未知")
    sentiment_score = sentiment.get("score", 50)
    sentiment_color = "#22c55e" if sentiment_score >= 55 else ("#f59e0b" if sentiment_score >= 40 else "#ef4444")
    sentiment_html = f"""
    <div style="margin-top:16px;">
        <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:8px;">市场情绪</div>
        <div style="display:flex;align-items:center;gap:12px;">
            <div style="font-size:1.2rem;font-weight:700;color:{sentiment_color};">{sentiment_phase}</div>
            <div style="font-size:0.8rem;color:#94a3b8;">情绪评分 {sentiment_score}/100</div>
        </div>
        <div style="margin-top:4px;font-size:0.75rem;color:#94a3b8;">{sentiment.get('suggestion', '')}</div>
    </div>
    """

    # ── 综合建议 ──
    reason_list = ""
    for rs in reasons[:6]:
        reason_list += f'<li style="margin-bottom:4px;color:#94a3b8;font-size:0.8rem;">{rs}</li>'

    stop_loss_html = f"""
    <div style="margin-top:10px;padding:10px;background:rgba(239,68,68,0.06);border:1px solid rgba(239,68,68,0.15);border-radius:8px;">
        <div style="font-size:0.75rem;color:#f87171;font-weight:600;">建议止损位: {stop_loss} 元</div>
    </div>
    """ if stop_loss else ""

    report_html = f"""
    <div style="border:1px solid #1e293b;border-radius:14px;overflow:hidden;background:#0b1120;">
        <!-- 头部 -->
        <div style="padding:1.2rem 1.4rem;background:linear-gradient(135deg, rgba(59,130,246,0.08) 0%, rgba(168,85,247,0.05) 100%);border-bottom:1px solid #1e293b;">
            <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:10px;">
                <div>
                    <div style="font-size:1.1rem;font-weight:700;color:#f8fafc;">{name} <span style="color:#64748b;font-size:0.85rem;font-weight:500;">{ts_code}</span></div>
                    <div style="margin-top:4px;font-size:0.85rem;color:#94a3b8;">
                        现价 <span style="color:#f8fafc;font-weight:600;">{safe_fmt(price)}</span>
                        <span style="color:{pct_color};margin-left:6px;font-weight:600;">{pct_sign}{safe_fmt(change_pct, '{:+.2f}')}%</span>
                        <span style="margin-left:12px;color:#64748b;font-size:0.75rem;">换手 {safe_fmt(quote.get('turnover'))}% | 量比 {safe_fmt(qt_tech.get('volume_ratio'))}</span>
                    </div>
                </div>
                <div style="text-align:right;">
                    <div style="display:inline-flex;align-items:center;gap:8px;padding:6px 14px;border-radius:20px;background:{hex_to_rgba(grade_color, 0.12)};border:1px solid {hex_to_rgba(grade_color, 0.2)};">
                        <span style="font-size:1.3rem;font-weight:800;color:{grade_color};">{grade}</span>
                        <span style="font-size:0.8rem;color:{grade_color};font-weight:600;">{grade_label}</span>
                    </div>
                    <div style="margin-top:4px;font-size:0.75rem;color:#64748b;">综合评分 {score}/100</div>
                </div>
            </div>
        </div>

        <!-- 技术指标 -->
        <div style="padding:1.2rem 1.4rem;border-bottom:1px solid #1e293b;">
            <div style="color:#64748b;font-size:0.7rem;font-weight:600;letter-spacing:1px;margin-bottom:12px;">主要技术指标</div>
            <div style="display:flex;gap:12px;flex-wrap:wrap;">
                {kdj_html}
                {macd_html}
                {boll_html}
                {ma_html}
            </div>
        </div>

        <!-- 信号 + 风险 + 情绪 -->
        <div style="padding:1.2rem 1.4rem;border-bottom:1px solid #1e293b;">
            <div style="display:flex;gap:20px;flex-wrap:wrap;">
                <div style="flex:1;min-width:220px;">
                    {dragon_html}
                </div>
                <div style="flex:1;min-width:220px;">
                    {risk_html}
                    {sentiment_html}
                </div>
            </div>
        </div>

        <!-- 综合判断 -->
        <div style="padding:1.2rem 1.4rem;background:rgba(59,130,246,0.03);">
            <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px;">
                <span style="font-size:1rem;font-weight:700;color:{action_color};">{action}</span>
                <span style="font-size:0.75rem;color:#64748b;">— 基于技术面综合研判</span>
            </div>
            <ul style="margin:0;padding-left:1.2rem;">
                {reason_list}
            </ul>
            {stop_loss_html}
            <div style="margin-top:12px;font-size:0.7rem;color:#475569;line-height:1.4;">
                风险提示：以上内容仅为基于历史数据和技术指标的分析，不构成任何投资建议。股市有风险，投资需谨慎。
            </div>
        </div>
    </div>
    """
    return report_html
