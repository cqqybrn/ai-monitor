"""技术指标 — 纯 pandas/numpy 实现，避免依赖

输入 DataFrame 必须有 close 列；MACD/RSI/Bollinger 等都基于 close。
所有函数返回新列追加到原 df 的副本上。
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def add_ma(df: pd.DataFrame, periods=(20, 50, 200)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"ma{p}"] = out["close"].rolling(p).mean()
    return out


def add_ema(df: pd.DataFrame, periods=(12, 26)) -> pd.DataFrame:
    out = df.copy()
    for p in periods:
        out[f"ema{p}"] = out["close"].ewm(span=p, adjust=False).mean()
    return out


def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    out = df.copy()
    ema_fast = out["close"].ewm(span=fast, adjust=False).mean()
    ema_slow = out["close"].ewm(span=slow, adjust=False).mean()
    out["macd"] = ema_fast - ema_slow
    out["macd_signal"] = out["macd"].ewm(span=signal, adjust=False).mean()
    out["macd_hist"] = out["macd"] - out["macd_signal"]
    return out


def add_rsi(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    delta = out["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out[f"rsi{period}"] = 100 - (100 / (1 + rs))
    return out


def add_bollinger(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> pd.DataFrame:
    out = df.copy()
    mid = out["close"].rolling(period).mean()
    sd = out["close"].rolling(period).std()
    out["bb_mid"] = mid
    out["bb_upper"] = mid + std * sd
    out["bb_lower"] = mid - std * sd
    return out


def add_atr(df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
    out = df.copy()
    prev_close = out["close"].shift(1)
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - prev_close).abs(),
        (out["low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    out[f"atr{period}"] = tr.ewm(alpha=1 / period, adjust=False).mean()
    return out


def add_all(df: pd.DataFrame) -> pd.DataFrame:
    """一把梭：加上常用指标"""
    out = add_ma(df)
    out = add_ema(out)
    out = add_macd(out)
    out = add_rsi(out)
    out = add_bollinger(out)
    out = add_atr(out)
    return out
