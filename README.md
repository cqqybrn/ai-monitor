# K 线数据分析

自动下载并分析 **币安永续合约** + **美股** 的 K 线数据。

## 特性

- 币安 USD-M 永续合约（fapi）— 公开 K 线无需 API key
- 美股 yfinance — 1h / 1d 直拉，2h / 4h 由 1h resample 生成
- Parquet 列式存储，支持增量合并
- 技术指标：MA / EMA / MACD / RSI / 布林带 / ATR（纯 pandas 实现）
- CLI 一键下载 + Streamlit 交互式 K 线图

## 快速开始

### 1. 装环境（venv）

```powershell
# Windows PowerShell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. 下载数据

```powershell
# 拉所有币安合约（BTCUSDT/ETHUSDT/SOLUSDT × 1h/2h/4h/1d）
python -m src.cli fetch-crypto

# 拉所有美股（AAPL/TSLA/NVDA × 1h/2h/4h/1d）
python -m src.cli fetch-stocks

# 一把梭
python -m src.cli fetch-all

# 查看末尾几行 + 指标
python -m src.cli show BTCUSDT 1h
```

数据落到 `data/crypto/*.parquet` 和 `data/stocks/*.parquet`。

### 3. 启动 Dashboard

```powershell
streamlit run app/dashboard.py
```

浏览器自动打开 `http://localhost:8501`。

## 项目结构

```
k/
├── config.py               # 标的、周期、路径配置
├── requirements.txt
├── data/                   # parquet 数据落盘
│   ├── crypto/
│   └── stocks/
├── src/
│   ├── fetchers/
│   │   ├── binance_futures.py  # 币安合约 K 线（fapi.binance.com）
│   │   └── yfinance_us.py      # 美股
│   ├── storage.py              # parquet 增量读写
│   ├── indicators.py           # 技术指标
│   └── cli.py                  # 命令行入口
└── app/
    └── dashboard.py            # streamlit dashboard
```

## 改配置

打开 [config.py](config.py)：

- `CRYPTO_SYMBOLS` — 加密合约标的（默认 BTC/ETH/SOL USDT 永续）
- `STOCK_SYMBOLS` — 美股标的（默认 AAPL/TSLA/NVDA）
- `INTERVALS` — K 线周期（默认 1h/2h/4h/1d）

## 注意事项

- **加密走的是合约接口** `fapi.binance.com/fapi/v1/klines`，不是现货 `api.binance.com`
- yfinance 1h 历史上限约 730 天，超出会报错
- 增量更新逻辑：每次拉取从本地最后一根 K 线 open_time 起拉，会覆盖最后一根（处理未收盘那根）
- 第一次跑 `fetch-crypto` 大约要 30 秒（拉 365 天 × 4 周期 × 3 标的）

## 后续可加

- 更多市场（港股、A 股、其他交易所合约）
- 选股 / 信号扫描
- 策略回测（vectorbt / backtrader）
- 实时 WebSocket 推送
