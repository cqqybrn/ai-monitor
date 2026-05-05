"""ZEC RF 4H 进 / 2H 出 — 1:1 翻译自 Pine Script

策略特征:
- 入场: 仅 4H Range Filter buy/sell pulse, 无任何过滤
- 出场: 4H 反转 / 2H 反向 RF / 15% 止损
- 多空对称, 默认双向开启
- 市价单 (无 limit), 0.1% 双边手续费
- 主图 2H, 4H 信号通过 lookahead_off 拉入
"""
from __future__ import annotations
from dataclasses import dataclass
import pandas as pd

from .range_filter import rf_signal


@dataclass
class ZecParams:
    rf_period: int = 100
    rf_mult: float = 3.0
    mc_pct: float = 0.15
    commission: float = 0.001
    enable_short: bool = True


def align_4h_to_2h(df_2h: pd.DataFrame, df_4h_with_sig: pd.DataFrame) -> pd.DataFrame:
    """对齐 4H 信号到 2H bar (无未来函数: 4H bar 只在它收盘后才能被 2H 看到)"""
    h = df_4h_with_sig[["open_time", "buy4", "sell4"]].copy()
    h["available_at"] = h["open_time"] + 4 * 3_600_000
    h = h.sort_values("available_at")
    m = df_2h.sort_values("open_time").reset_index(drop=True)
    merged = pd.merge_asof(m, h.drop(columns=["open_time"]),
                            left_on="open_time", right_on="available_at",
                            direction="backward")
    merged["buy4"] = merged["buy4"].fillna(False).astype(bool)
    merged["sell4"] = merged["sell4"].fillna(False).astype(bool)
    return merged.drop(columns=["available_at"])


def build_signal_frame(df_2h: pd.DataFrame, df_4h: pd.DataFrame,
                        params: ZecParams) -> pd.DataFrame:
    df_2h = df_2h.sort_values("open_time").reset_index(drop=True).copy()
    df_4h = df_4h.sort_values("open_time").reset_index(drop=True).copy()

    rf_2h = rf_signal(df_2h["close"], params.rf_period, params.rf_mult)
    df_2h["buy2"] = rf_2h["buy"].values
    df_2h["sell2"] = rf_2h["sell"].values

    rf_4h = rf_signal(df_4h["close"], params.rf_period, params.rf_mult)
    df_4h["buy4"] = rf_4h["buy"].values
    df_4h["sell4"] = rf_4h["sell"].values

    return align_4h_to_2h(df_2h, df_4h)


def run_strategy(signals: pd.DataFrame, params: ZecParams):
    """状态机 — 严格按 Pine 代码顺序: 入场 → 出场检查"""
    from src.backtest import BacktestResult, Trade
    rows = signals.to_dict("records")
    n = len(rows)

    pos_dir = 0
    entry_price = 0.0
    entry_bar = -1
    equity = 1.0
    trades: list[Trade] = []
    equity_curve: list[tuple[int, float]] = []

    def _close(bar_idx: int, price: float, reason: str):
        nonlocal pos_dir, entry_price, entry_bar, equity
        if pos_dir == 0:
            return
        gross = (price - entry_price) / entry_price * pos_dir
        # commission_value=0.1% 双边 (entry + exit 各扣 0.1%)
        net = (1 + gross) * (1 - params.commission) ** 2 - 1
        equity *= (1 + net)
        trades.append(Trade(
            entry_bar=entry_bar, exit_bar=bar_idx,
            entry_price=entry_price, exit_price=price,
            direction="long" if pos_dir > 0 else "short",
            ret=net, equity_after=equity, exit_reason=reason,
        ))
        pos_dir = 0
        entry_price = 0.0
        entry_bar = -1

    def _enter(bar_idx: int, price: float, direction: int, reason: str):
        nonlocal pos_dir, entry_price, entry_bar
        if pos_dir != 0 and pos_dir != direction:
            _close(bar_idx, price, f"reversal_to_{reason}")
        if pos_dir == 0:
            pos_dir = direction
            entry_price = price
            entry_bar = bar_idx

    for i in range(n):
        row = rows[i]
        ot = row["open_time"]
        h, l, c = row["high"], row["low"], row["close"]

        # 1. 止损 (intrabar fill at stop level, 比 Pine 原版稍优 — 真正的 stop order 假设)
        if pos_dir > 0 and entry_bar < i:
            mc_long = entry_price * (1 - params.mc_pct)
            if l <= mc_long:
                _close(i, mc_long, "mc_stop")
        elif pos_dir < 0 and entry_bar < i:
            mc_short = entry_price * (1 + params.mc_pct)
            if h >= mc_short:
                _close(i, mc_short, "mc_stop")

        # 2. 4H 入场 (auto-flip 反向持仓)
        if row["buy4"]:
            _enter(i, c, 1, "4h_buy")
        elif row["sell4"]:
            if params.enable_short:
                _enter(i, c, -1, "4h_sell")
            elif pos_dir > 0:
                _close(i, c, "4h_sell_no_short")

        # 3. 出场 (Pine 顺序: sell4 → sell2 → mc, 但前面 sell4 已处理过)
        if pos_dir > 0 and entry_bar < i:
            if row["sell2"]:
                _close(i, c, "2h_opp")
        elif pos_dir < 0 and entry_bar < i:
            if row["buy2"]:
                _close(i, c, "2h_opp")

        cur_eq = equity if pos_dir == 0 else equity * (1 + (c - entry_price) / entry_price * pos_dir)
        equity_curve.append((ot, cur_eq))

    if pos_dir != 0:
        _close(n - 1, rows[-1]["close"], "end_of_data")

    return BacktestResult(trades=trades, equity_curve=equity_curve, params=params)
