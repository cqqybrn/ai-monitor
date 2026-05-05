"""币安 USD-M 永续合约 K 线下载器

走 fapi.binance.com（合约），不是 api.binance.com（现货）。
公开 K 线接口不需要 API key。

Binance 单次返回上限 1500 根 K 线，所以历史回拉需要分页。
"""
from __future__ import annotations
import time
from typing import Optional
import requests
import pandas as pd

from config import BINANCE_FUTURES_BASE, BINANCE_KLINE_LIMIT

KLINES_ENDPOINT = "/fapi/v1/klines"

INTERVAL_MS = {
    "1m": 60_000, "3m": 180_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
    "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000,
}


def _request_klines(symbol: str, interval: str, start_ms: int, limit: int) -> list[list]:
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": start_ms,
        "limit": limit,
    }
    url = BINANCE_FUTURES_BASE + KLINES_ENDPOINT
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def fetch_klines(
    symbol: str,
    interval: str,
    start_ms: int,
    end_ms: Optional[int] = None,
) -> pd.DataFrame:
    """从 start_ms 拉到 end_ms（默认 now），自动分页。

    返回 DataFrame: open_time, open, high, low, close, volume, close_time
    open_time/close_time 都是毫秒 epoch。
    """
    if interval not in INTERVAL_MS:
        raise ValueError(f"unsupported interval: {interval}")
    if end_ms is None:
        end_ms = int(time.time() * 1000)

    step_ms = INTERVAL_MS[interval]
    all_rows: list[list] = []
    cursor = start_ms

    while cursor < end_ms:
        batch = _request_klines(symbol, interval, cursor, BINANCE_KLINE_LIMIT)
        if not batch:
            break
        all_rows.extend(batch)
        last_open = batch[-1][0]
        next_cursor = last_open + step_ms
        if next_cursor <= cursor:
            break
        cursor = next_cursor
        if len(batch) < BINANCE_KLINE_LIMIT:
            break
        time.sleep(0.1)

    if not all_rows:
        return pd.DataFrame(columns=["open_time", "open", "high", "low", "close", "volume", "close_time"])

    df = pd.DataFrame(all_rows, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ])
    df = df[["open_time", "open", "high", "low", "close", "volume", "close_time"]].copy()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    df["open_time"] = df["open_time"].astype("int64")
    df["close_time"] = df["close_time"].astype("int64")
    return df


def fetch_recent(symbol: str, interval: str, lookback_days: int = 365) -> pd.DataFrame:
    start_ms = int(time.time() * 1000) - lookback_days * 86_400_000
    return fetch_klines(symbol, interval, start_ms)
