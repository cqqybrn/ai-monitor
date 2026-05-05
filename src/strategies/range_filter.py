"""Range Filter + ADX/DMI + CHOP — 1:1 翻译自 Pine v6

关键: Pine 的 ta.ema 和 ta.rma 与 pandas ewm 的等价写法:
- ta.ema(src, n)  ≡  src.ewm(span=n, adjust=False)
- ta.rma(src, n)  ≡  src.ewm(alpha=1/n, adjust=False)   (Wilder smoothing)
"""
from __future__ import annotations
import numpy as np
import pandas as pd


def smoothrng(close: pd.Series, period: int, mult: float) -> pd.Series:
    """ta.ema(ta.ema(abs(x - x[1]), t), t*2 - 1) * m"""
    diff = close.diff().abs()
    e1 = diff.ewm(span=period, adjust=False).mean()
    e2 = e1.ewm(span=period * 2 - 1, adjust=False).mean()
    return e2 * mult


def rngfilt(close: pd.Series, r: pd.Series) -> pd.Series:
    """状态依赖滤波器 - Pine 的 rngfilt 函数

    f := nz(f[1], x)
    f := x > f ? max(f, x - r) : min(f, x + r)
    """
    x = close.to_numpy()
    rv = r.to_numpy()
    n = len(x)
    f = np.empty(n)
    if n == 0:
        return pd.Series(f, index=close.index)
    f[0] = x[0]
    for i in range(1, n):
        prev = f[i - 1]
        if not np.isfinite(prev):
            prev = x[i]
        if x[i] > prev:
            f[i] = max(prev, x[i] - rv[i])
        elif x[i] < prev:
            f[i] = min(prev, x[i] + rv[i])
        else:
            f[i] = prev
    return pd.Series(f, index=close.index)


def rf_signal(close: pd.Series, period: int = 100, mult: float = 3.0) -> pd.DataFrame:
    """复刻 Pine 的 rf_sig() — 返回 buy_pulse / sell_pulse 两列

    返回 DataFrame: filter, lc, sc, st, buy, sell
    buy/sell 是 PULSE（趋势翻转那根 bar 才 True，其他都 False）
    """
    r = smoothrng(close, period, mult)
    f = rngfilt(close, r)

    # up/dn 计数器
    f_diff = f.diff()
    up = np.zeros(len(close))
    dn = np.zeros(len(close))
    f_arr = f.to_numpy()
    for i in range(1, len(close)):
        if f_arr[i] > f_arr[i - 1]:
            up[i] = up[i - 1] + 1
            dn[i] = 0
        elif f_arr[i] < f_arr[i - 1]:
            dn[i] = dn[i - 1] + 1
            up[i] = 0
        else:
            up[i] = up[i - 1]
            dn[i] = dn[i - 1]

    c = close.to_numpy()
    lc = (c > f_arr) & (up > 0)
    sc = (c < f_arr) & (dn > 0)

    st = np.zeros(len(close), dtype=int)
    for i in range(len(close)):
        prev = st[i - 1] if i > 0 else 0
        if lc[i]:
            st[i] = 1
        elif sc[i]:
            st[i] = -1
        else:
            st[i] = prev

    st_prev = np.concatenate([[0], st[:-1]])
    buy = lc & (st_prev == -1)
    sell = sc & (st_prev == 1)

    return pd.DataFrame({
        "filter": f,
        "lc": lc,
        "sc": sc,
        "st": st,
        "buy": buy,
        "sell": sell,
    }, index=close.index)


def rma(src: pd.Series, length: int) -> pd.Series:
    """Pine-exact ta.rma — Wilder smoothing with SMA seed.

    - 前 length-1 个有效 bar: 输出 NaN
    - 第 length 个有效 bar: 输出 = SMA(src, length) 作为种子
    - 之后: rma[i] = (rma[i-1] * (length-1) + src[i]) / length

    与 pandas ewm(alpha=1/n, adjust=False) 的递推公式相同, 但种子值不同
    (后者用 src 的第一个有效值, 前者用前 length 个值的均值)
    """
    arr = src.to_numpy(dtype=float)
    n = len(arr)
    out = np.full(n, np.nan)
    if n == 0 or length < 1:
        return pd.Series(out, index=src.index)

    valid = ~np.isnan(arr)
    if valid.sum() < length:
        return pd.Series(out, index=src.index)

    first_valid = int(np.argmax(valid))
    seed_end = first_valid + length - 1
    seed_window = arr[first_valid:seed_end + 1]
    if np.any(np.isnan(seed_window)):
        return pd.Series(out, index=src.index)

    seed = float(seed_window.mean())
    out[seed_end] = seed
    prev = seed
    inv = 1.0 / length
    for i in range(seed_end + 1, n):
        v = arr[i]
        if np.isnan(v):
            out[i] = np.nan
        else:
            prev = prev * (1.0 - inv) + v * inv
            out[i] = prev
    return pd.Series(out, index=src.index)


def dmi_adx(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14):
    """复刻 Pine ta.dmi(diLength=length, adxSmoothing=length)

    用 Pine-exact RMA (SMA 种子), 与 TradingView 数学完全对齐.
    """
    up_move = high.diff()
    down_move = -low.diff()

    plus_dm_raw = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm_raw = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm_raw, index=high.index)
    minus_dm = pd.Series(minus_dm_raw, index=high.index)

    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    atr = rma(tr, length)
    plus_di = 100 * rma(plus_dm, length) / atr
    minus_di = 100 * rma(minus_dm, length) / atr

    di_sum = (plus_di + minus_di).replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / di_sum
    adx = rma(dx, length)

    return plus_di, minus_di, adx


def chop_index(high: pd.Series, low: pd.Series, close: pd.Series, length: int = 14) -> pd.Series:
    """Choppiness Index — 复刻 Pine 公式

    chop = 100 * log10( sum(atr(1), len) / (highest(high,len) - lowest(low,len)) ) / log10(len)
    Pine 的 atr(1) 实际就是 TR (true range with length=1)
    """
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    sum_tr = tr.rolling(length).sum()
    hh = high.rolling(length).max()
    ll = low.rolling(length).min()
    rng = (hh - ll).clip(lower=1e-12)
    return 100.0 * np.log10(sum_tr / rng) / np.log10(length)
