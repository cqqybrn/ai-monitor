"""中盘标的 A 级信号实战评估

对每个标的:
  1. 跑完整 RF + 多TF 评级 (Tier 1+2+3, walk-forward)
  2. 仅多, 取 ≥A 级信号
  3. 计算: A 信号笔数 / 胜率 / Calmar / 期望 / 平均最大浮盈
  4. 同时给出 ≥B 级数据作为对比 (大样本 backup)
  5. 按可信度+质量综合排名
"""
import sys; sys.path.insert(0, '.')
import pandas as pd
import numpy as np
from src import storage
from config import CRYPTO_SYMBOLS, STOCK_SYMBOLS, CRYPTO_DIR, STOCK_DIR
from src.monitor import build_watch_frame, detect_signal_flips


def evaluate_grade(symbol: str, base_dir, min_grade: str = "A",
                    long_only: bool = True, commission: float = 0.001):
    main = storage.load(base_dir, symbol, "4h")
    if main.empty: return None
    extras = {}
    for tf in ("1h", "2h", "1d"):
        d = storage.load(base_dir, symbol, tf)
        if not d.empty: extras[tf] = (d, tf)
    if not extras: return None
    try:
        watch = build_watch_frame(main, "4h", extras, 100, 3.0)
        states = detect_signal_flips(watch, list(extras.keys()), use_priors=True)
    except Exception as e:
        return {"error": str(e)}
    closed = [s for s in states if s.bars_since_entry >= 1]
    if not closed: return None

    grade_rank = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
    min_r = grade_rank[min_grade]

    eq = 1.0; peak = 1.0; max_dd = 0.0
    rets = []; max_favs = []; max_advs = []; wins = 0
    for s in closed:
        if grade_rank.get(s.grade, 0) < min_r: continue
        if long_only and s.direction != "long": continue
        ret = s.cur_unrealized
        net = (1 + ret) * (1 - commission) ** 2 - 1
        eq *= (1 + net); peak = max(peak, eq)
        max_dd = min(max_dd, (eq / peak) - 1)
        rets.append(net); max_favs.append(s.max_favorable); max_advs.append(s.max_adverse)
        if net > 0: wins += 1
    n = len(rets)
    if n == 0:
        return {"n": 0}
    span_years = (closed[-1].entry_time_ms - closed[0].entry_time_ms) / 1000 / 86400 / 365.25
    cagr = eq ** (1 / max(span_years, 0.01)) - 1
    return {
        "n": n,
        "ret_pct": (eq - 1) * 100,
        "cagr_pct": cagr * 100,
        "max_dd_pct": max_dd * 100,
        "calmar": (cagr / abs(max_dd)) if max_dd < 0 else float("inf"),
        "win_rate": wins / n * 100,
        "expectancy_pct": np.mean(rets) * 100,
        "avg_max_fav_pct": np.mean(max_favs) * 100,
        "avg_max_adv_pct": np.mean(max_advs) * 100,
    }


SECTOR = {
    # 加密
    "BTCUSDT": ("crypto", "锚"),
    "ZECUSDT": ("crypto", "隐私"),
    "DASHUSDT": ("crypto", "隐私"),
    "AAVEUSDT": ("crypto", "DeFi"),
    "INJUSDT": ("crypto", "DeFi/L1"),
    "AVAXUSDT": ("crypto", "L1"),
    "HYPEUSDT": ("crypto", "Perp DEX"),
    "TIAUSDT": ("crypto", "模块化"),
    "SUIUSDT": ("crypto", "L1"),
    "WLDUSDT": ("crypto", "AI/身份"),
    "NEARUSDT": ("crypto", "AI"),
    "ICPUSDT": ("crypto", "AI/老币"),
    "FILUSDT": ("crypto", "存储/老币"),
    "HBARUSDT": ("crypto", "企业链"),
    # 美股
    "QQQ":   ("stock", "锚"),
    "WDC":   ("stock", "存储"),
    "STX":   ("stock", "存储"),
    "MU":    ("stock", "存储"),
    "SNDK":  ("stock", "存储"),
    "NTAP":  ("stock", "存储"),
    "DELL":  ("stock", "存储/服务器"),
    "PSTG":  ("stock", "存储"),
    "CIEN":  ("stock", "光模块"),
    "LITE":  ("stock", "光模块"),
    "COHR":  ("stock", "光模块"),
    "AAOI":  ("stock", "光模块"),
    "FN":    ("stock", "光模块"),
}


def main():
    rows_a = []
    rows_b = []
    print(f"评测 {len(CRYPTO_SYMBOLS) + len(STOCK_SYMBOLS)} 个中盘标的 ...")
    for sym in CRYPTO_SYMBOLS:
        a = evaluate_grade(sym, CRYPTO_DIR, "A")
        b = evaluate_grade(sym, CRYPTO_DIR, "B")
        if a: a["symbol"] = sym; a["sector"] = SECTOR.get(sym, ("crypto", "?"))[1]
        if b: b["symbol"] = sym; b["sector"] = SECTOR.get(sym, ("crypto", "?"))[1]
        rows_a.append(a); rows_b.append(b)
    for sym in STOCK_SYMBOLS:
        a = evaluate_grade(sym, STOCK_DIR, "A")
        b = evaluate_grade(sym, STOCK_DIR, "B")
        if a: a["symbol"] = sym; a["sector"] = SECTOR.get(sym, ("stock", "?"))[1]
        if b: b["symbol"] = sym; b["sector"] = SECTOR.get(sym, ("stock", "?"))[1]
        rows_a.append(a); rows_b.append(b)

    def _print(rows: list, label: str):
        valid = [r for r in rows if r and r.get("n", 0) > 0]
        valid.sort(key=lambda r: (r.get("calmar", -999) if r.get("calmar") != float("inf") else 999), reverse=True)
        print()
        print("=" * 100)
        print(f"  {label}")
        print("=" * 100)
        print(f"{'Symbol':<10} {'板块':<12} {'N':>4} {'胜率%':>6} {'ret%':>8} {'CAGR%':>7} {'回撤%':>7} {'Calmar':>7} {'Expect%':>8} {'avgFav%':>8} {'avgAdv%':>8}")
        print("-" * 100)
        for r in valid:
            cal = r.get("calmar", 0)
            cal_s = f"{cal:+7.2f}" if cal != float("inf") else "    inf"
            print(f"{r['symbol']:<10} {r['sector']:<12} {r['n']:>4} "
                  f"{r['win_rate']:>5.1f} {r['ret_pct']:>+7.1f} {r['cagr_pct']:>+6.1f} {r['max_dd_pct']:>+6.1f} "
                  f"{cal_s} {r['expectancy_pct']:>+7.2f} {r['avg_max_fav_pct']:>+7.2f} {r['avg_max_adv_pct']:>+7.2f}")

    _print(rows_a, "≥A 级信号实战 (long-only, 0.1% 双边手续费)")
    _print(rows_b, "≥B 级信号实战 (大样本对照)")

    # 综合排名: 同时考虑样本量和质量
    print()
    print("=" * 100)
    print("  综合可信度排名 (≥A 级, 用 sqrt(N) × max(Calmar, 0) 评分)")
    print("=" * 100)
    scored = []
    for r in rows_a:
        if not r or r.get("n", 0) == 0: continue
        cal = r.get("calmar", 0)
        if cal == float("inf"): cal = 99
        score = (r["n"] ** 0.5) * max(cal, 0)
        scored.append((score, r))
    scored.sort(reverse=True)
    print(f"{'排名':<4} {'Symbol':<10} {'板块':<12} {'N':>3} {'Calmar':>7} {'CAGR%':>7} {'回撤%':>6} {'综合分':>7}")
    for i, (sc, r) in enumerate(scored[:25], 1):
        cal = r.get("calmar", 0)
        cal_s = f"{cal:+7.2f}" if cal != float("inf") else "    inf"
        print(f"{i:<4} {r['symbol']:<10} {r['sector']:<12} {r['n']:>3} {cal_s} {r['cagr_pct']:>+6.1f} {r['max_dd_pct']:>+5.1f} {sc:>+6.2f}")


if __name__ == "__main__":
    main()
