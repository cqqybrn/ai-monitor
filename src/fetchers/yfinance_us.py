"""美股 K 线下载器（yfinance）

yfinance 原生只支持 1h 和 1d，没有 2h/4h。
策略：拉 1h 然后 resample 到 2h/4h。日线直接拉。

注意 yfinance 1h 数据 history 上限大约 730 天。
"""
from __future__ import annotations
import pandas as pd
import yfinance as yf

from config import YF_HISTORY_DAYS_1H, YF_HISTORY_DAYS_1D


def _to_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """把 yfinance 返回的 DataFrame 转成统一 schema (毫秒时间戳)

    重要: 修复 phantom OHLC tick (不删除整根 bar):
      - 美股盘后 yfinance 经常 volume=0 但 close 是真实的 (财报后跳涨等)
      - 但偶尔会有错误 tick 导致 low/high 离谱 (low=$121 但 close=$414)
      - 修补策略: low/high 异常时, 用 min/max(open, close) 替代
    """
    if df.empty:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"})
    idx = pd.to_datetime(df.index, utc=True).as_unit("ns")
    df["open_time"] = idx.astype("int64") // 1_000_000
    out = df.reset_index(drop=True)[["open_time", "open", "high", "low", "close", "volume"]].copy()
    if out.empty:
        return out
    # 修复 phantom OHLC: 如果 low/high 离 (open, close) 区间太远, 用 (open, close) 修补
    oc_min = out[["open", "close"]].min(axis=1)
    oc_max = out[["open", "close"]].max(axis=1)
    # low 不应低于 min(open, close) 的 70% (允许 30% 区间内波动)
    bad_low = out["low"] < oc_min * 0.7
    # high 不应高于 max(open, close) 的 130%
    bad_high = out["high"] > oc_max * 1.3
    n_fixed = int(bad_low.sum() + bad_high.sum())
    if n_fixed > 0:
        out.loc[bad_low, "low"] = oc_min[bad_low]
        out.loc[bad_high, "high"] = oc_max[bad_high]
        print(f"  ⚠️ 修复 {n_fixed} 根 phantom OHLC tick (low/high 异常, 保留 open/close)")
    return out


def _add_close_time(df: pd.DataFrame, interval_ms: int) -> pd.DataFrame:
    df = df.copy()
    df["close_time"] = df["open_time"] + interval_ms - 1
    return df


def fetch_1h(symbol: str, days: int = YF_HISTORY_DAYS_1H) -> pd.DataFrame:
    """1H 美股 K 线 — 含盘前盘后 (prepost=True) 拓展每日 6.5h → 16h"""
    period = f"{min(days, 730)}d"
    raw = yf.download(symbol, period=period, interval="1h", progress=False,
                      auto_adjust=False, prepost=True)
    df = _to_ohlcv(raw)
    return _add_close_time(df, 3_600_000)


def fetch_1d(symbol: str, days: int = YF_HISTORY_DAYS_1D) -> pd.DataFrame:
    """日线 — 不含盘前盘后, 因为日线是会话级聚合"""
    period = f"{days}d"
    raw = yf.download(symbol, period=period, interval="1d", progress=False,
                      auto_adjust=False)
    df = _to_ohlcv(raw)
    return _add_close_time(df, 86_400_000)


def resample_from_1h(df_1h: pd.DataFrame, target: str) -> pd.DataFrame:
    """把 1h K 线 resample 到 2h / 4h / 8h。"""
    if df_1h.empty:
        return df_1h
    rule_map = {"2h": "2h", "4h": "4h", "8h": "8h"}
    if target not in rule_map:
        raise ValueError(f"unsupported resample target: {target}")
    interval_ms = {"2h": 7_200_000, "4h": 14_400_000, "8h": 28_800_000}[target]

    df = df_1h.copy()
    df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.as_unit("ns")
    df = df.set_index("dt")

    agg = df.resample(rule_map[target], label="left", closed="left").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }).dropna(subset=["open"])

    idx = pd.DatetimeIndex(agg.index).as_unit("ns")
    agg["open_time"] = idx.astype("int64") // 1_000_000
    agg = agg.reset_index(drop=True)
    return _add_close_time(agg[["open_time", "open", "high", "low", "close", "volume"]], interval_ms)


def fetch(symbol: str, interval: str) -> pd.DataFrame:
    """统一入口：1h/1d 直拉，2h/4h/8h 通过 resample"""
    if interval == "1h":
        return fetch_1h(symbol)
    if interval == "1d":
        return fetch_1d(symbol)
    if interval in ("2h", "4h", "8h"):
        return resample_from_1h(fetch_1h(symbol), interval)
    raise ValueError(f"unsupported interval for US stocks: {interval}")
