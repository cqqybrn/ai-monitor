"""ZEC 4H 进 / 2H 出 双 EMA 策略 — 1:1 翻译自 Pine v6

入场: 4H EMA20 上穿 EMA50 (golden cross)
出场: 2H EMA20 下穿 EMA50 (death cross)
仅多, 100% equity, 0.1% 双边手续费, 无止损
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd


@dataclass
class EmaCrossParams:
    fast_len: int = 20
    slow_len: int = 50
    commission: float = 0.001


def _crossover(fast: pd.Series, slow: pd.Series) -> pd.Series:
    """ta.crossover: fast > slow AND fast[1] <= slow[1] (单次脉冲)"""
    return (fast > slow) & (fast.shift(1) <= slow.shift(1))


def _crossunder(fast: pd.Series, slow: pd.Series) -> pd.Series:
    return (fast < slow) & (fast.shift(1) >= slow.shift(1))


def build_signal_frame(df_2h: pd.DataFrame, df_4h: pd.DataFrame,
                        params: EmaCrossParams) -> pd.DataFrame:
    df_2h = df_2h.sort_values("open_time").reset_index(drop=True).copy()
    df_4h = df_4h.sort_values("open_time").reset_index(drop=True).copy()

    # 4H EMA + 金叉
    df_4h["ema_fast"] = df_4h["close"].ewm(span=params.fast_len, adjust=False).mean()
    df_4h["ema_slow"] = df_4h["close"].ewm(span=params.slow_len, adjust=False).mean()
    df_4h["cross_up_4h"] = _crossover(df_4h["ema_fast"], df_4h["ema_slow"])

    # 2H EMA + 死叉
    df_2h["ema_fast_2h"] = df_2h["close"].ewm(span=params.fast_len, adjust=False).mean()
    df_2h["ema_slow_2h"] = df_2h["close"].ewm(span=params.slow_len, adjust=False).mean()
    df_2h["cross_dn_2h"] = _crossunder(df_2h["ema_fast_2h"], df_2h["ema_slow_2h"])

    # 4H 信号对齐到 2H (no lookahead: 4H 在 open_time + 4h 后才可见)
    h = df_4h[["open_time", "cross_up_4h"]].copy()
    h["available_at"] = h["open_time"] + 4 * 3_600_000
    h = h.sort_values("available_at")
    merged = pd.merge_asof(df_2h, h.drop(columns=["open_time"]),
                            left_on="open_time", right_on="available_at",
                            direction="backward")
    merged["cross_up_4h"] = merged["cross_up_4h"].fillna(False).astype(bool)
    # 4H 金叉对齐后会在 2 根 2H bar 上都为 True; 取首根作为入场脉冲
    merged["entry_pulse"] = merged["cross_up_4h"] & ~merged["cross_up_4h"].shift(1, fill_value=False)
    return merged.drop(columns=["available_at"])


def run_strategy(signals: pd.DataFrame, params: EmaCrossParams):
    from src.backtest import BacktestResult, Trade
    rows = signals.to_dict("records")
    n = len(rows)

    pos = 0
    entry_price = 0.0
    entry_bar = -1
    equity = 1.0
    trades: list[Trade] = []
    equity_curve: list[tuple[int, float]] = []

    def _close(bar_idx: int, price: float, reason: str):
        nonlocal pos, entry_price, entry_bar, equity
        if pos == 0:
            return
        gross = (price - entry_price) / entry_price
        net = (1 + gross) * (1 - params.commission) ** 2 - 1
        equity *= (1 + net)
        trades.append(Trade(
            entry_bar=entry_bar, exit_bar=bar_idx,
            entry_price=entry_price, exit_price=price,
            direction="long",
            ret=net, equity_after=equity, exit_reason=reason,
        ))
        pos = 0
        entry_price = 0.0
        entry_bar = -1

    for i in range(n):
        row = rows[i]
        ot = row["open_time"]
        c = row["close"]

        # 入场: 4H 金叉脉冲, 仅在空仓时触发
        if row["entry_pulse"] and pos == 0:
            pos = 1
            entry_price = c
            entry_bar = i

        # 出场: 2H 死叉, 不能跟入场同根 bar
        if pos == 1 and row["cross_dn_2h"] and entry_bar < i:
            _close(i, c, "2h_death_cross")

        cur_eq = equity if pos == 0 else equity * (1 + (c - entry_price) / entry_price)
        equity_curve.append((ot, cur_eq))

    if pos > 0:
        _close(n - 1, rows[-1]["close"], "end_of_data")

    return BacktestResult(trades=trades, equity_curve=equity_curve, params=params)
