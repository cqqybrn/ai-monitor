"""信号状态监控 — 仿"懂币猫"机器人

评分系统:
  入场那一刻一次性锁定 (S/A/B/C/D, 0-100 分), 之后整个信号生命周期不变.
  评分由三部分组成:
    - ADX(14) at entry        (0-30 分): 趋势强度, 高 ADX = 强趋势
    - 多 TF 对齐 at entry     (0-50 分): 更高/更低 TF 同向数量
    - Range Filter 斜率       (0-20 分): 滤波线变化率, 反映趋势加速度
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional
import numpy as np
import pandas as pd

from src.strategies.range_filter import rf_signal, dmi_adx, chop_index


@dataclass
class SignalState:
    direction: str
    entry_time_ms: int
    entry_price: float
    score: int = 0
    grade: str = "C"
    entry_adx: float = 0.0
    aligned_tfs: list[str] = field(default_factory=list)
    score_breakdown: dict = field(default_factory=dict)
    max_favorable: float = 0.0
    max_adverse: float = 0.0
    cur_unrealized: float = 0.0
    cur_price: float = 0.0
    cur_time_ms: int = 0
    bars_since_entry: int = 0

    def update(self, time_ms: int, price: float):
        self.cur_time_ms = time_ms
        self.cur_price = price
        ret = (price - self.entry_price) / self.entry_price
        if self.direction == "short":
            ret = -ret
        self.cur_unrealized = ret
        self.max_favorable = max(self.max_favorable, ret)
        self.max_adverse = min(self.max_adverse, ret)
        self.bars_since_entry += 1


def _trend_state(rf_df: pd.DataFrame) -> pd.Series:
    """从 rf_signal 输出还原每根 bar 的趋势状态: long/short
    用 st 列: st==1 -> long, st==-1 -> short, st==0 -> 未定 (返回 None)
    """
    st = rf_df["st"]
    return st.map({1: "long", -1: "short", 0: None})


def _align_state_to_main(main_df: pd.DataFrame, other_df: pd.DataFrame,
                         other_state: pd.Series, other_interval_ms: int,
                         label: str) -> pd.Series:
    """把另一 TF 的 trend state 列对齐到 main 的每根 bar (右侧) - 防未来函数

    other_df 的某根 bar (open_time=t) 在 t + other_interval_ms 时收盘可见.
    对每根 main bar (open_time=T), 取 available_at = t + dt <= T 的最新 state.
    """
    o = pd.DataFrame({"open_time": other_df["open_time"], "state": other_state.values})
    o["available_at"] = o["open_time"] + other_interval_ms
    o = o.sort_values("available_at").dropna(subset=["state"])

    m = main_df[["open_time"]].sort_values("open_time").reset_index(drop=True)
    merged = pd.merge_asof(m, o[["available_at", "state"]],
                            left_on="open_time", right_on="available_at",
                            direction="backward")
    return merged["state"].rename(f"state_{label}")


def _add_tier2_features(df: pd.DataFrame, vol_window: int = 200,
                         vol_avg_window: int = 20, ma_window: int = 50) -> pd.DataFrame:
    """Tier 2 量化上下文: ATR 分位 / CHOP / 量比 / 价格 z-score"""
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.ewm(alpha=1 / 14, adjust=False).mean()
    out["atr_pct"] = atr14.rolling(vol_window).rank(pct=True) * 100
    out["chop"] = chop_index(out["high"], out["low"], out["close"], 14).values
    vol_avg = out["volume"].rolling(vol_avg_window).mean()
    out["vol_ratio"] = out["volume"] / vol_avg.replace(0, np.nan)
    ma = out["close"].rolling(ma_window).mean()
    sd = out["close"].rolling(ma_window).std()
    out["price_z"] = (out["close"] - ma) / sd.replace(0, np.nan)
    return out


def build_watch_frame(df_main: pd.DataFrame, main_interval: str,
                       extras: dict[str, tuple[pd.DataFrame, str]],
                       rf_period: int = 100, rf_mult: float = 3.0) -> pd.DataFrame:
    """构建监控用的主 frame (一般是 4H), 每根 bar 含:
      - 主 TF 自身的 buy/sell pulse + filter + ADX (Tier 1)
      - 对齐进来的其他 TF 的 trend state (Tier 1: state_1h / state_1d 等)
      - Tier 2: atr_pct / chop / vol_ratio / price_z
    """
    df = df_main.sort_values("open_time").reset_index(drop=True).copy()
    rf = rf_signal(df["close"], rf_period, rf_mult)
    df["buy"] = rf["buy"].values
    df["sell"] = rf["sell"].values
    df["filter"] = rf["filter"].values
    df["filter_slope"] = df["filter"].pct_change()
    _, _, adx = dmi_adx(df["high"], df["low"], df["close"], 14)
    df["adx"] = adx.values

    interval_ms = {"1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000,
                    "8h": 28_800_000, "1d": 86_400_000}
    for label, (extra_df, extra_interval) in extras.items():
        ed = extra_df.sort_values("open_time").reset_index(drop=True).copy()
        rf_e = rf_signal(ed["close"], rf_period, rf_mult)
        state = _trend_state(rf_e)
        aligned = _align_state_to_main(df, ed, state,
                                         interval_ms[extra_interval], label)
        df[f"state_{label}"] = aligned.values

    df = _add_tier2_features(df)
    return df


def _safe_float(val, default: float = 0.0) -> float:
    if val is None or pd.isna(val):
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _tier1_score(direction: str, row: dict, tf_labels: list[str]) -> tuple[float, list[str]]:
    """Tier 1 现场证据分 (0-100): ADX 30 + 多TF对齐 50 + RF斜率 20"""
    adx = _safe_float(row.get("adx"), 0.0)
    adx_score = min(max(adx, 0), 50) / 50 * 30
    aligned = [tf for tf in tf_labels if str(row.get(f"state_{tf}", None)) == direction]
    align_score = (len(aligned) / max(len(tf_labels), 1)) * 50
    slope = _safe_float(row.get("filter_slope"), 0.0)
    slope_score = min(abs(slope) * 5000, 20)
    return adx_score + align_score + slope_score, aligned


def _tier2_score(row: dict) -> tuple[float, dict]:
    """Tier 2 量化上下文 (0-100): 波动分位 25 + CHOP 25 + 量比 25 + 价格延展 25"""
    breakdown = {}

    atr_pct = row.get("atr_pct")
    atr_pct = float(atr_pct) if atr_pct is not None and not pd.isna(atr_pct) else 50.0
    vol_score = min(max(atr_pct, 0), 100) / 100 * 25
    breakdown["波动分位"] = round(vol_score, 1)

    chop = row.get("chop")
    chop = float(chop) if chop is not None and not pd.isna(chop) else 50.0
    chop_score = max(0.0, min(1.0, (62.0 - chop) / (62.0 - 38.0))) * 25
    breakdown["趋势性(CHOP)"] = round(chop_score, 1)

    vol_ratio = row.get("vol_ratio")
    vol_ratio = float(vol_ratio) if vol_ratio is not None and not pd.isna(vol_ratio) else 1.0
    vol_conf_score = min(vol_ratio / 2.0, 1.0) * 25
    breakdown["量能确认"] = round(vol_conf_score, 1)

    z = row.get("price_z")
    z = abs(float(z)) if z is not None and not pd.isna(z) else 0.0
    if z < 0.5:
        ext_score = 0.0
    elif z <= 2.5:
        ext_score = (z - 0.5) / 2.0 * 25
    else:
        ext_score = max(0.0, 25.0 - (z - 2.5) * 10.0)
    breakdown["价格延展"] = round(ext_score, 1)

    return vol_score + chop_score + vol_conf_score + ext_score, breakdown


def _grade_from_score(s: int) -> str:
    if s >= 85: return "S"
    if s >= 70: return "A"
    if s >= 55: return "B"
    if s >= 40: return "C"
    return "D"


def compute_entry_score(direction: str, row: dict, tf_labels: list[str],
                         priors: dict | None = None) -> tuple[str, int, list[str], dict]:
    """触发时锁定评分

    无 priors:  Tier1 50% + Tier2 50%
    有 priors:  Tier1 40% + Tier2 30% + Tier3 30% (历史同桶胜率)
    """
    t1, aligned = _tier1_score(direction, row, tf_labels)
    t2, t2_breakdown = _tier2_score(row)

    if priors is None:
        final = int(0.5 * t1 + 0.5 * t2)
        t3 = None
        prior_n = 0
    else:
        key = (direction, _bucket_t1(t1), _bucket_t2(t2))
        bucket = priors.get(key)
        if bucket is None or bucket["n"] < 5:
            t3 = 50.0
            prior_n = bucket["n"] if bucket else 0
        else:
            t3 = bucket["win_rate"] * 100
            prior_n = bucket["n"]
        final = int(0.4 * t1 + 0.3 * t2 + 0.3 * t3)

    grade = _grade_from_score(final)
    breakdown = {
        "tier1_total": int(t1),
        "tier2_total": int(t2),
        "tier2": t2_breakdown,
    }
    if t3 is not None:
        breakdown["tier3_total"] = int(t3)
        breakdown["tier3_n"] = prior_n
    return grade, final, aligned, breakdown


def detect_signal_flips(watch_df: pd.DataFrame,
                          tf_labels: list[str],
                          use_priors: bool = False,
                          min_bars_to_close: int = 6,
                          win_threshold: float = 0.02) -> list[SignalState]:
    """从 watch frame 还原所有翻转信号 + 在触发那一刻锁定评分

    use_priors=True: walk-forward 累积历史信号 → 当下信号查"同桶历史胜率"做 Tier 3 评分
                     严格无 look-ahead: 信号 i 评分时只用 i 之前的信号
    """
    states: list[SignalState] = []
    cur: Optional[SignalState] = None
    bucket_groups: dict[tuple, list[SignalState]] = {}

    def _build_priors() -> dict:
        out = {}
        for k, group in bucket_groups.items():
            n = len(group)
            wins = sum(1 for s in group if s.max_favorable > win_threshold)
            out[k] = {
                "n": n,
                "win_rate": wins / n,
                "avg_max_fav": float(np.mean([s.max_favorable for s in group])),
            }
        return out

    def _add_to_priors(s: SignalState):
        if s.bars_since_entry < min_bars_to_close:
            return
        bd = s.score_breakdown
        key = (s.direction, _bucket_t1(bd.get("tier1_total", 0)),
               _bucket_t2(bd.get("tier2_total", 0)))
        bucket_groups.setdefault(key, []).append(s)

    for _, row in watch_df.iterrows():
        ot = int(row["open_time"])
        price = float(row["close"])
        if cur is not None:
            cur.update(ot, price)

        if row["buy"] or row["sell"]:
            if use_priors and cur is not None:
                _add_to_priors(cur)
            priors = _build_priors() if use_priors else None
            direction = "long" if row["buy"] else "short"
            grade, score, aligned, breakdown = compute_entry_score(
                direction, row.to_dict(), tf_labels, priors=priors)
            cur = SignalState(
                direction=direction, entry_time_ms=ot, entry_price=price,
                score=score, grade=grade,
                entry_adx=float(row.get("adx", 0.0) or 0.0),
                aligned_tfs=aligned, score_breakdown=breakdown,
                cur_price=price, cur_time_ms=ot,
            )
            states.append(cur)

    return states


def format_signal_card(symbol: str, state: SignalState, interval: str,
                        sector: str = "加密主流",
                        capital: float = 77000.0,
                        tz_offset_hours: int = 8) -> str:
    """仿"懂币猫"格式生成 Telegram 卡片文本 (评分用 entry-time 静态值)"""
    direction_emoji = "🟢" if state.direction == "long" else "🔴"
    direction_label = "做多 (Long)" if state.direction == "long" else "做空 (Short)"

    entry_dt_local = (datetime.fromtimestamp(state.entry_time_ms / 1000, tz=timezone.utc)
                      + timedelta(hours=tz_offset_hours))
    entry_str = entry_dt_local.strftime("%Y-%m-%d %H:%M:%S")

    elapsed_ms = state.cur_time_ms - state.entry_time_ms
    duration_str = _format_duration(elapsed_ms)

    pnl_abs = state.cur_unrealized * capital
    max_fav_abs = state.max_favorable * capital
    max_adv_abs = state.max_adverse * capital

    if state.max_adverse >= -1e-6:
        max_dd_str = "暂未出现 (暂未出现)"
    else:
        max_dd_str = f"{state.max_adverse * 100:+.2f}% ({max_adv_abs:+.2f})"

    aligned_str = ("/".join(state.aligned_tfs) if state.aligned_tfs else "无")
    bd = state.score_breakdown
    t1 = bd.get("tier1_total", 0)
    t2 = bd.get("tier2_total", 0)
    t3 = bd.get("tier3_total")
    t3_n = bd.get("tier3_n", 0)
    t2d = bd.get("tier2", {})
    score_summary = f"(T1={t1} T2={t2}" + (f" T3={t3} n={t3_n})" if t3 is not None else ")")

    lines = [
        f"⏰ 定时复盘: {symbol}",
        "",
        f"所属板块: {sector}",
        f"信号标签: {'方向切换' if state.bars_since_entry < 6 else '持续跟踪'}",
        f"信号强度: {state.grade}级  {state.score}分  {score_summary}",
        f"  └ T1: ADX={state.entry_adx:.1f}, 同向TF={aligned_str}",
        f"  └ T2: 波动={t2d.get('波动分位', 0)} CHOP={t2d.get('趋势性(CHOP)', 0)} 量能={t2d.get('量能确认', 0)} 延展={t2d.get('价格延展', 0)}",
    ]
    if t3 is not None:
        lines.append(f"  └ T3: 同桶历史胜率={t3}% (n={t3_n})")
    lines += [
        f"当前维持方向: {direction_emoji} {direction_label}",
        f"信号已持续: {duration_str}",
        f"触发时入场价: {state.entry_price:.2f}",
        f"实时现价: {state.cur_price:.2f}",
        f"自首发以来涨跌: {state.cur_unrealized * 100:+.2f}%",
        f"按当前方向浮盈: {state.cur_unrealized * 100:+.2f}% ({pnl_abs:+.2f})",
        f"最大浮盈: {state.max_favorable * 100:+.2f}% ({max_fav_abs:+.2f})",
        f"最大回撤: {max_dd_str}",
        f"信号首发时间: {entry_str}",
        f"图表时间周期: {interval}",
        "",
        f"#{symbol}  #定时复盘",
    ]
    return "\n".join(lines)


def _bucket_t1(t1: float) -> str:
    if t1 >= 80: return "T1≥80"
    if t1 >= 60: return "T1 60-80"
    if t1 >= 40: return "T1 40-60"
    return "T1<40"


def _bucket_t2(t2: float) -> str:
    if t2 >= 70: return "T2≥70"
    if t2 >= 50: return "T2 50-70"
    if t2 >= 30: return "T2 30-50"
    return "T2<30"


def analyze_history(states: list[SignalState], min_bars: int = 6,
                     win_threshold: float = 0.02) -> dict:
    """对历史信号分桶, 统计胜率/平均最大浮盈/平均最大回撤

    win_threshold: 最大浮盈超过多少算"胜" (默认 2%, 一倍止损 2.5% 的级别)
    min_bars: 持续 < min_bars 的信号丢弃 (太短无法评估)
    """
    by_grade = {}
    by_bucket = {}
    for s in states:
        if s.bars_since_entry < min_bars:
            continue
        g = s.grade
        by_grade.setdefault(g, []).append(s)
        bd = s.score_breakdown
        key = (s.direction, _bucket_t1(bd.get("tier1_total", 0)),
               _bucket_t2(bd.get("tier2_total", 0)))
        by_bucket.setdefault(key, []).append(s)

    def _stats(group: list[SignalState]) -> dict:
        n = len(group)
        if n == 0:
            return {"n": 0}
        wins = sum(1 for s in group if s.max_favorable > win_threshold)
        max_favs = [s.max_favorable for s in group]
        max_advs = [s.max_adverse for s in group]
        finals = [s.cur_unrealized for s in group]
        return {
            "n": n,
            "win_rate": wins / n,
            "avg_max_fav": float(np.mean(max_favs)),
            "avg_max_adv": float(np.mean(max_advs)),
            "avg_final": float(np.mean(finals)),
            "expectancy": float(np.mean(max_favs) + np.mean(max_advs)),
        }

    grade_stats = {g: _stats(group) for g, group in by_grade.items()}
    bucket_stats = {k: _stats(group) for k, group in by_bucket.items()}
    return {"by_grade": grade_stats, "by_bucket": bucket_stats}


def historical_priors(states: list[SignalState], min_bars: int = 6,
                       win_threshold: float = 0.02,
                       min_bucket_n: int = 5) -> dict:
    """构建分桶 → 胜率字典 (供 Tier 3 评分查询)
    返回: {(direction, t1_range, t2_range): win_rate} - 仅包含样本足够的桶
    """
    bucket_groups: dict[tuple, list[SignalState]] = {}
    for s in states:
        if s.bars_since_entry < min_bars:
            continue
        bd = s.score_breakdown
        key = (s.direction, _bucket_t1(bd.get("tier1_total", 0)),
               _bucket_t2(bd.get("tier2_total", 0)))
        bucket_groups.setdefault(key, []).append(s)

    priors = {}
    for k, group in bucket_groups.items():
        if len(group) < min_bucket_n:
            continue
        wins = sum(1 for s in group if s.max_favorable > win_threshold)
        priors[k] = {
            "n": len(group),
            "win_rate": wins / len(group),
            "avg_max_fav": float(np.mean([s.max_favorable for s in group])),
        }
    return priors


def _format_duration(ms: int) -> str:
    if ms < 0:
        ms = 0
    days = ms // 86_400_000
    rem = ms % 86_400_000
    hours = rem // 3_600_000
    rem2 = rem % 3_600_000
    minutes = rem2 // 60_000
    parts = []
    if days > 0:
        parts.append(f"{days}天")
    if hours > 0 or days == 0:
        parts.append(f"{hours}小时")
    if days == 0 and hours < 1 and minutes > 0:
        parts.append(f"{minutes}分钟")
    return "".join(parts) if parts else "刚刚"
