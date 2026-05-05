"""Parquet 数据落盘 — 按 symbol_interval.parquet 分文件，支持增量合并"""
from __future__ import annotations
from pathlib import Path
import pandas as pd

KLINE_COLUMNS = ["open_time", "open", "high", "low", "close", "volume", "close_time"]


def parquet_path(base_dir: Path, symbol: str, interval: str) -> Path:
    return base_dir / f"{symbol}_{interval}.parquet"


def load(base_dir: Path, symbol: str, interval: str) -> pd.DataFrame:
    p = parquet_path(base_dir, symbol, interval)
    if not p.exists():
        return pd.DataFrame(columns=KLINE_COLUMNS)
    return pd.read_parquet(p)


def save(df: pd.DataFrame, base_dir: Path, symbol: str, interval: str) -> Path:
    base_dir.mkdir(parents=True, exist_ok=True)
    p = parquet_path(base_dir, symbol, interval)
    df.to_parquet(p, index=False)
    return p


def merge_incremental(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    """合并旧数据和新拉取的数据，按 open_time 去重，保留新的（修正未收盘那根）"""
    if old.empty:
        return new.sort_values("open_time").reset_index(drop=True)
    if new.empty:
        return old
    combined = pd.concat([old, new], ignore_index=True)
    combined = combined.drop_duplicates(subset=["open_time"], keep="last")
    return combined.sort_values("open_time").reset_index(drop=True)


def last_open_time_ms(df: pd.DataFrame) -> int | None:
    """返回最后一根 K 线的 open_time（毫秒），用于增量起点。空表返回 None"""
    if df.empty:
        return None
    return int(df["open_time"].iloc[-1])
