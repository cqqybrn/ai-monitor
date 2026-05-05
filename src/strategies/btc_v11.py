"""BTC v11 策略 — 1:1 翻译自 Pine Script

策略核心:
  入场(多): 4H趋势=long  + 1H RF买入  + ADX上升(adx > adx[3])
            限价挂单 plPx = (high + low) / 2 (在信号 bar 上)
  出场(多): 4H趋势反转 / 止损 -2.5% / 2H RF卖出
  空单: 同上对称, 用 CHOP+ADX 过滤, 默认关闭

关键: 高时间框信号通过 "available_at = open_time + interval_ms" 对齐到 1H,
      避免未来函数 (等价 Pine 的 request.security lookahead_off).
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from .range_filter import rf_signal, dmi_adx, chop_index


@dataclass
class StratParams:
    rf_period: int = 100
    rf_mult: float = 3.0
    adx_len: int = 14
    adx_lookback: int = 3
    sl_pct: float = 0.025
    chop_len: int = 14
    short_chop_max: float = 37.0
    short_adx_max: float = 28.0
    enable_short: bool = False


def align_higher_tf(df_low: pd.DataFrame, df_high: pd.DataFrame, htf_interval_ms: int,
                     cols: list[str]) -> pd.DataFrame:
    """把 df_high 的 cols 列对齐到 df_low 的 bar 上 (无未来函数)

    df_high 的某根 bar (open_time=t) 在它收盘的瞬间 (t + htf_interval_ms) 才能被低 TF 看到.
    所以对每根 1H bar (open_time=T), 取 available_at = t + htf_ms <= T 的最新 bar.
    """
    h = df_high[["open_time"] + cols].copy()
    h["available_at"] = h["open_time"] + htf_interval_ms
    h = h.sort_values("available_at")
    low_sorted = df_low.sort_values("open_time").reset_index(drop=True)
    merged = pd.merge_asof(
        low_sorted,
        h.drop(columns=["open_time"]),
        left_on="open_time",
        right_on="available_at",
        direction="backward",
    )
    return merged.drop(columns=["available_at"])


def build_signal_frame(df_1h: pd.DataFrame, df_2h: pd.DataFrame, df_4h: pd.DataFrame,
                       params: StratParams) -> pd.DataFrame:
    """生成回测需要的所有信号列, index = 1H bar 顺序"""
    df_1h = df_1h.sort_values("open_time").reset_index(drop=True).copy()
    df_2h = df_2h.sort_values("open_time").reset_index(drop=True).copy()
    df_4h = df_4h.sort_values("open_time").reset_index(drop=True).copy()

    rf_1h = rf_signal(df_1h["close"], params.rf_period, params.rf_mult)
    df_1h["h1L"] = rf_1h["buy"].values
    df_1h["h1S"] = rf_1h["sell"].values

    plus_di, minus_di, adx = dmi_adx(df_1h["high"], df_1h["low"], df_1h["close"], params.adx_len)
    df_1h["adx"] = adx.values
    df_1h["adx_rising"] = (adx > adx.shift(params.adx_lookback)).values

    df_1h["chop"] = chop_index(df_1h["high"], df_1h["low"], df_1h["close"], params.chop_len).values

    rf_2h = rf_signal(df_2h["close"], params.rf_period, params.rf_mult)
    df_2h["h2L"] = rf_2h["buy"].values
    df_2h["h2S"] = rf_2h["sell"].values

    rf_4h = rf_signal(df_4h["close"], params.rf_period, params.rf_mult)
    df_4h["h4L"] = rf_4h["buy"].values
    df_4h["h4S"] = rf_4h["sell"].values

    aligned = align_higher_tf(df_1h, df_2h, htf_interval_ms=2 * 3_600_000,
                               cols=["h2L", "h2S"])
    aligned = align_higher_tf(aligned, df_4h, htf_interval_ms=4 * 3_600_000,
                               cols=["h4L", "h4S"])

    aligned["h2L"] = aligned["h2L"].fillna(False).astype(bool)
    aligned["h2S"] = aligned["h2S"].fillna(False).astype(bool)
    aligned["h4L"] = aligned["h4L"].fillna(False).astype(bool)
    aligned["h4S"] = aligned["h4S"].fillna(False).astype(bool)

    aligned["h4LP"] = aligned["h4L"] & ~aligned["h4L"].shift(1, fill_value=False)
    aligned["h4SP"] = aligned["h4S"] & ~aligned["h4S"].shift(1, fill_value=False)

    return aligned


def run_strategy(signals: pd.DataFrame, params: StratParams) -> "BacktestResult":
    """状态机回测 — 严格复刻 Pine 的执行顺序"""
    from src.backtest import BacktestResult, Trade
    rows = signals.to_dict("records")
    n = len(rows)

    trend = "flat"
    placed = False
    pl_long = True
    pl_px: float | None = None
    sig_bar: int | None = None
    pos_size = 0.0
    pos_dir = 0
    entry_price = 0.0
    entry_bar = -1
    equity = 1.0
    trades: list[Trade] = []
    equity_curve: list[tuple[int, float]] = []

    def _close(bar_idx: int, price: float, reason: str):
        nonlocal pos_size, pos_dir, equity, entry_price, entry_bar
        if pos_dir == 0:
            return
        ret = (price - entry_price) / entry_price * pos_dir
        equity *= (1 + ret)
        trades.append(Trade(
            entry_bar=entry_bar, exit_bar=bar_idx,
            entry_price=entry_price, exit_price=price,
            direction="long" if pos_dir > 0 else "short",
            ret=ret, equity_after=equity, exit_reason=reason,
        ))
        pos_size = 0.0
        pos_dir = 0
        entry_price = 0.0
        entry_bar = -1

    for i in range(n):
        row = rows[i]
        ot = row["open_time"]
        o, h, l, c = row["open"], row["high"], row["low"], row["close"]

        if pos_dir != 0:
            if pos_dir > 0:
                stop_px = entry_price * (1 - params.sl_pct)
                if l <= stop_px and entry_bar < i:
                    _close(i, stop_px, "stop_loss")
            else:
                stop_px = entry_price * (1 + params.sl_pct)
                if h >= stop_px and entry_bar < i:
                    _close(i, stop_px, "stop_loss")

        if placed and pos_dir == 0 and sig_bar is not None and i > sig_bar:
            if pl_long and pl_px is not None and l <= pl_px <= h:
                pos_size = 1.0
                pos_dir = 1
                entry_price = pl_px
                entry_bar = i
                placed = False
                pl_px = None
                stop_px = entry_price * (1 - params.sl_pct)
                if l <= stop_px:
                    _close(i, stop_px, "stop_loss_same_bar")
            elif (not pl_long) and pl_px is not None and l <= pl_px <= h:
                pos_size = 1.0
                pos_dir = -1
                entry_price = pl_px
                entry_bar = i
                placed = False
                pl_px = None
                stop_px = entry_price * (1 + params.sl_pct)
                if h >= stop_px:
                    _close(i, stop_px, "stop_loss_same_bar")

        prev_trend = trend
        if row["h4LP"]:
            trend = "long"
        if row["h4SP"]:
            trend = "short" if params.enable_short else "flat"
        t_chg = trend != prev_trend

        if t_chg and pos_dir != 0:
            is_l = pos_dir > 0
            want_l = trend == "long"
            if is_l != want_l:
                _close(i, c, "4h_reversal")
                placed = False
                pl_px = None

        can_exit = (entry_bar < 0) or (i > entry_bar)

        if pos_dir > 0 and can_exit and row["h2S"]:
            _close(i, c, "2h_sell")
            placed = False
            pl_px = None

        if params.enable_short and pos_dir < 0 and can_exit and row["h1L"]:
            _close(i, c, "1h_buy")
            placed = False
            pl_px = None

        if placed and pos_dir == 0:
            if pl_long and row["h2S"]:
                placed = False
                pl_px = None
            elif params.enable_short and (not pl_long) and row["h1L"]:
                placed = False
                pl_px = None

        can_pl = pos_dir == 0 and not placed and trend != "flat"
        if can_pl and trend == "long" and row["h1L"] and row["adx_rising"]:
            pl_px = (h + l) / 2.0
            pl_long = True
            sig_bar = i
            placed = True
        if (params.enable_short and can_pl and trend == "short"
                and row["h1S"] and row["chop"] <= params.short_chop_max
                and row["adx"] <= params.short_adx_max):
            pl_px = (h + l) / 2.0
            pl_long = False
            sig_bar = i
            placed = True

        equity_curve.append((ot, equity if pos_dir == 0 else equity * (1 + (c - entry_price) / entry_price * pos_dir)))

    if pos_dir != 0:
        last_close = rows[-1]["close"]
        _close(n - 1, last_close, "end_of_data")

    return BacktestResult(
        trades=trades,
        equity_curve=equity_curve,
        params=params,
    )
