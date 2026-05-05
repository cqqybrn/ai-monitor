"""回测引擎数据结构 + 绩效指标"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import math
import numpy as np
import pandas as pd


@dataclass
class Trade:
    entry_bar: int
    exit_bar: int
    entry_price: float
    exit_price: float
    direction: str
    ret: float
    equity_after: float
    exit_reason: str


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity_curve: list[tuple[int, float]]
    params: Any

    def equity_df(self) -> pd.DataFrame:
        if not self.equity_curve:
            return pd.DataFrame(columns=["open_time", "equity"])
        df = pd.DataFrame(self.equity_curve, columns=["open_time", "equity"])
        df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        return df

    def trade_df(self) -> pd.DataFrame:
        if not self.trades:
            return pd.DataFrame()
        return pd.DataFrame([t.__dict__ for t in self.trades])

    def metrics(self) -> dict[str, float]:
        eq = self.equity_df()
        if eq.empty:
            return {}
        total_return = eq["equity"].iloc[-1] - 1.0
        peak = eq["equity"].cummax()
        dd = (eq["equity"] / peak - 1.0)
        max_dd = float(dd.min())

        trades = self.trade_df()
        wins = (trades["ret"] > 0).sum() if len(trades) else 0
        n = len(trades)
        win_rate = wins / n if n > 0 else 0.0
        avg_win = trades.loc[trades["ret"] > 0, "ret"].mean() if wins > 0 else 0.0
        avg_loss = trades.loc[trades["ret"] <= 0, "ret"].mean() if (n - wins) > 0 else 0.0

        first_t = eq["open_time"].iloc[0]
        last_t = eq["open_time"].iloc[-1]
        years = max((last_t - first_t) / 1000.0 / 86400.0 / 365.25, 1e-9)
        cagr = (eq["equity"].iloc[-1]) ** (1 / years) - 1
        calmar = cagr / abs(max_dd) if max_dd < 0 else float("inf")

        eq["ret"] = eq["equity"].pct_change().fillna(0.0)
        bars_per_year = 24 * 365
        sharpe = (eq["ret"].mean() / eq["ret"].std() * math.sqrt(bars_per_year)) if eq["ret"].std() > 0 else 0.0

        gp = trades.loc[trades["ret"] > 0, "ret"].sum() if wins > 0 else 0.0
        gl = -trades.loc[trades["ret"] <= 0, "ret"].sum() if (n - wins) > 0 else 0.0
        pf = gp / gl if gl > 0 else float("inf")

        return {
            "total_return_pct": total_return * 100,
            "cagr_pct": cagr * 100,
            "max_drawdown_pct": max_dd * 100,
            "calmar": calmar,
            "sharpe": sharpe,
            "profit_factor": pf,
            "trades": int(n),
            "win_rate_pct": win_rate * 100,
            "avg_win_pct": float(avg_win) * 100 if avg_win else 0.0,
            "avg_loss_pct": float(avg_loss) * 100 if avg_loss else 0.0,
            "years": years,
        }
