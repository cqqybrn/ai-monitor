"""项目配置 — 改这里调标的、周期、路径"""
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
CRYPTO_DIR = DATA_DIR / "crypto"
STOCK_DIR = DATA_DIR / "stocks"

CRYPTO_SYMBOLS = [
    # 一流锚 (基准, 不主推)
    "BTCUSDT",
    # 中盘叙事 - 隐私复兴
    "ZECUSDT", "DASHUSDT",
    # 中盘叙事 - DeFi 蓝筹 / 衍生品
    "AAVEUSDT", "INJUSDT", "AVAXUSDT",
    # 中盘叙事 - 新主线 (AI / 模块化 / Perp DEX)
    "HYPEUSDT", "TIAUSDT", "SUIUSDT", "WLDUSDT", "NEARUSDT",
    # 中盘叙事 - 老币复活
    "ICPUSDT", "FILUSDT", "HBARUSDT",
]
STOCK_SYMBOLS = [
    # 一流锚 (基准, 不主推)
    "QQQ",
    # 存储
    "WDC", "STX", "MU", "SNDK", "NTAP", "DELL", "PSTG",
    # 光模块 / 网络设备 (AI 数据中心 + 5G)
    "CIEN", "LITE", "COHR", "AAOI", "FN", "NOK", "CRDO", "ALAB",
    # CPU / 处理器 + IP
    "INTC", "AMD", "QCOM", "ARM",  # QCOM (汽车芯片 + AI PC + 5G modem narrative)
    # 半导体设备 (光刻 / 蚀刻 / 测量)
    "KLAC", "LRCX", "AMAT", "ASML",
    # 晶圆代工
    "TSM",
    # 模拟 / 工业 / 汽车芯片 (SOXX 大权重)
    "TXN", "MCHP", "ON", "NXPI", "MPWR",
    # AI 网络芯片 / 高速 IO
    "MRVL",
    # AI 服务器 / 系统集成
    "SMCI", "HPE",
    # AI 软件
    "AI",
    # 数据中心电力 / 冷却
    "VRT", "GEV", "ETN",
    # AI 云基础设施 (GPU 租赁)
    "NBIS", "CRWV",
    # === 2026 Q2 新加 ===
    # 防务 / 无人机
    "AVAV",
    # 商业航天
    "RKLB",
    # AI 实物机器人 / 仓储自动化
    "SYM",
    # 医疗 AI / 药物发现
    "TEM",
    # 量子计算 (高 beta)
    "IONQ",
    # BTC 矿企 / AI 数据中心租赁
    "WULF",
    # 5G/通信设备 (NOK 同类候选)
    "ERIC", "VIAV",
]

INTERVALS = ["1h", "2h", "4h", "8h", "1d"]

BINANCE_FUTURES_BASE = "https://fapi.binance.com"
BINANCE_KLINE_LIMIT = 1500

YF_NATIVE_INTERVALS = {"1h": "1h", "1d": "1d"}
YF_RESAMPLE_FROM_1H = {"2h": "2h", "4h": "4h"}

YF_HISTORY_DAYS_1H = 720
YF_HISTORY_DAYS_1D = 365 * 10
