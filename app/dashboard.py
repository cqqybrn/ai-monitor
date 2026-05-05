"""Streamlit Dashboard — 浏览器看 K 线 + 指标

启动：
    streamlit run app/dashboard.py
"""
from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

from config import CRYPTO_SYMBOLS, STOCK_SYMBOLS, INTERVALS, CRYPTO_DIR, STOCK_DIR
from src import storage, indicators

st.set_page_config(page_title="K 线分析", layout="wide")
st.title("K 线数据分析")

with st.sidebar:
    st.header("选择标的")
    market = st.radio("市场", ["加密合约", "美股"], horizontal=True)
    if market == "加密合约":
        symbols = CRYPTO_SYMBOLS
        base_dir = CRYPTO_DIR
    else:
        symbols = STOCK_SYMBOLS
        base_dir = STOCK_DIR

    symbol = st.selectbox("Symbol", symbols)
    interval = st.selectbox("周期", INTERVALS, index=0)
    bars = st.slider("显示最近 N 根", 50, 1000, 300)

    st.divider()
    st.subheader("叠加指标")
    show_ma = st.checkbox("MA 20/50/200", value=True)
    show_bb = st.checkbox("布林带 (20, 2σ)", value=False)
    show_rsi = st.checkbox("RSI 14", value=True)
    show_macd = st.checkbox("MACD", value=True)


df = storage.load(base_dir, symbol, interval)
if df.empty:
    st.warning(f"没有 {symbol} {interval} 的数据。请先在终端运行：")
    st.code("python -m src.cli fetch-crypto" if market == "加密合约" else "python -m src.cli fetch-stocks")
    st.stop()

df = indicators.add_all(df).tail(bars).reset_index(drop=True)
df["dt"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)

c1, c2, c3, c4 = st.columns(4)
last = df.iloc[-1]
prev = df.iloc[-2] if len(df) > 1 else last
chg = (last["close"] - prev["close"]) / prev["close"] * 100
c1.metric("最新价", f"{last['close']:.4f}", f"{chg:+.2f}%")
c2.metric("RSI(14)", f"{last['rsi14']:.1f}" if pd.notna(last.get("rsi14")) else "—")
c3.metric("MA20", f"{last['ma20']:.4f}" if pd.notna(last.get("ma20")) else "—")
c4.metric("数据条数", str(len(df)))

rows = 1 + (1 if show_rsi else 0) + (1 if show_macd else 0)
heights = [0.6] + [0.2] * (rows - 1) if rows > 1 else [1.0]
fig = make_subplots(rows=rows, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=heights)

fig.add_trace(go.Candlestick(
    x=df["dt"], open=df["open"], high=df["high"], low=df["low"], close=df["close"],
    name=symbol,
), row=1, col=1)

if show_ma:
    for col, color in [("ma20", "#ff7f0e"), ("ma50", "#2ca02c"), ("ma200", "#d62728")]:
        if col in df.columns:
            fig.add_trace(go.Scatter(x=df["dt"], y=df[col], mode="lines", name=col.upper(),
                                     line=dict(width=1, color=color)), row=1, col=1)

if show_bb:
    fig.add_trace(go.Scatter(x=df["dt"], y=df["bb_upper"], mode="lines", name="BB Upper",
                             line=dict(width=1, color="rgba(150,150,150,0.6)", dash="dot")), row=1, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["bb_lower"], mode="lines", name="BB Lower",
                             line=dict(width=1, color="rgba(150,150,150,0.6)", dash="dot"),
                             fill="tonexty", fillcolor="rgba(150,150,150,0.08)"), row=1, col=1)

current_row = 2
if show_rsi:
    fig.add_trace(go.Scatter(x=df["dt"], y=df["rsi14"], mode="lines", name="RSI14",
                             line=dict(color="#9467bd", width=1)), row=current_row, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="red", row=current_row, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="green", row=current_row, col=1)
    current_row += 1

if show_macd:
    colors = ["#26a69a" if v >= 0 else "#ef5350" for v in df["macd_hist"].fillna(0)]
    fig.add_trace(go.Bar(x=df["dt"], y=df["macd_hist"], name="MACD Hist", marker_color=colors),
                  row=current_row, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["macd"], mode="lines", name="MACD",
                             line=dict(color="#1f77b4", width=1)), row=current_row, col=1)
    fig.add_trace(go.Scatter(x=df["dt"], y=df["macd_signal"], mode="lines", name="Signal",
                             line=dict(color="#ff7f0e", width=1)), row=current_row, col=1)

fig.update_layout(
    height=200 + 400 * (heights[0]) + 200 * (rows - 1),
    xaxis_rangeslider_visible=False,
    margin=dict(l=10, r=10, t=30, b=10),
    showlegend=True,
    template="plotly_dark",
)
st.plotly_chart(fig, use_container_width=True)

with st.expander("查看原始数据（最近 50 行）"):
    cols_to_show = ["dt", "open", "high", "low", "close", "volume", "ma20", "rsi14", "macd_hist"]
    cols_to_show = [c for c in cols_to_show if c in df.columns]
    st.dataframe(df[cols_to_show].tail(50), use_container_width=True)
