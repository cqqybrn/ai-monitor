"""信号路由器 — 扫描 watchlist, 检测新 ≥B 信号, 推送到 Discord

策略锁定:
  入场: ≥B 评级 long 信号
  出场: ≥B 评级 short 信号
  watchlist: 24 个美股 AI/科技/存储/光模块
  每根 K 线收盘后扫描, 新信号推送 (用 .signal_state.json 去重)
"""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone, timedelta
from src import storage
from src.monitor import build_watch_frame, detect_signal_flips
from src.notifier import send_signal_card, send_text, send_summary
from config import STOCK_DIR


# 锁定的 watchlist (27 个) + 各自最优 TF
# 2026 Q2 调整: 淘汰 PLTR/SOUN/CEG/ADI, 新加 CRWV/AVAV/RKLB/SYM/TEM/IONQ/WULF
WATCHLIST = {
    # === 存储 ===
    "SNDK": ("8h", "存储"),
    "DELL": ("8h", "存储"),
    "MU":   ("4h", "存储"),
    "WDC":  ("8h", "存储"),
    "STX":  ("4h", "存储"),
    # === 光模块 / 光网络 ===
    "CRDO": ("8h", "光模块"),
    "CIEN": ("8h", "光网络"),
    "LITE": ("8h", "光模块"),
    "AAOI": ("8h", "光模块"),
    "COHR": ("4h", "光模块"),
    "NOK":  ("8h", "5G/光网络"),
    # === AI 云基础设施 ===
    "NBIS": ("4h", "AI云"),
    "CRWV": ("4h", "AI云"),       # ★新加
    # === CPU / 处理器 ===
    "AMD":  ("4h", "CPU"),
    "INTC": ("8h", "CPU"),
    # === 晶圆代工 / 半导设备 ===
    "TSM":  ("4h", "晶圆代工"),
    "LRCX": ("8h", "半导设备"),
    "AMAT": ("8h", "半导设备"),
    # === 模拟 / 微控制器 ===
    "TXN":  ("4h", "模拟"),
    "MCHP": ("8h", "微控制器"),
    # === 数据中心电力 ===
    "VRT":  ("4h", "数据中心电力"),
    # === 防务 / 航天 ===
    "AVAV": ("4h", "防务/无人机"),  # ★新加
    "RKLB": ("4h", "商业航天"),     # ★新加
    # === AI 实物机器人 / 医疗 AI ===
    "SYM":  ("4h", "AI机器人"),     # ★新加
    "TEM":  ("4h", "医疗AI"),       # ★新加
    # === BTC 矿 / AI 数据中心租赁 ===
    "WULF": ("4h", "BTC/AI数据中心"),  # ★新加
    # === AI 实物机器人 (narrative bet, 数据弱) ===
    "SYM":  ("4h", "AI机器人"),       # ★新加 (Calmar -0.10, 押热点)
    # === 医疗 AI / 药物发现 (narrative bet, 数据弱) ===
    "TEM":  ("4h", "医疗AI"),         # ★新加 (Calmar -0.37, 押热点)
    # === 光网络测试 (NOK 同类) ===
    "VIAV": ("8h", "光网络测试"),     # ★新加 (Calmar 1.04)
}

GRADE_RANK = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}
MIN_GRADE = "B"  # ≥B 才推送

STATE_FILE = Path(__file__).resolve().parent.parent / ".signal_state.json"


def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict):
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def _get_states_for(symbol: str, interval: str):
    """跑 build_watch_frame + detect_signal_flips, 返回 states + 当前价"""
    main = storage.load(STOCK_DIR, symbol, interval)
    if main.empty:
        return None, None, None
    main = main.sort_values("open_time").reset_index(drop=True)
    extras = {}
    for tf in ("1h", "2h", "4h", "8h", "1d"):
        if tf == interval:
            continue
        d = storage.load(STOCK_DIR, symbol, tf)
        if not d.empty:
            extras[tf] = (d, tf)
    try:
        watch = build_watch_frame(main, interval, extras, 100, 3.0)
        states = detect_signal_flips(watch, list(extras.keys()), use_priors=True)
    except Exception as e:
        return None, None, None
    cur_price = float(main.iloc[-1]["close"])
    cur_time = int(main.iloc[-1]["open_time"])
    return states, cur_price, cur_time


def scan_once(dry_run: bool = False, push_current: bool = False,
               max_history_hours: int = 48) -> dict:
    """扫一遍 watchlist, 推送新信号

    dry_run: 不发推送, 只打印
    push_current: 把所有"当前持仓中"的信号也推 (首次运行时用)
    max_history_hours: 状态空时, 只考虑过去 N 小时的信号 (避免首次推 1000 条历史)
    """
    state = _load_state()
    pushed_buy = 0; pushed_sell = 0
    summary_lines = []
    min_r = GRADE_RANK[MIN_GRADE]
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    history_cutoff_ms = now_ms - max_history_hours * 3_600_000

    for sym, (interval, sector) in WATCHLIST.items():
        states, cur_price, cur_time = _get_states_for(sym, interval)
        if states is None:
            continue

        last_pushed_ms = state.get(sym, {}).get("last_signal_ms", 0)
        last_action = state.get(sym, {}).get("last_action", "none")

        # 关键修复: 状态空 (first run) 时, 只考虑近 N 小时的信号, 避免历史信号洪水
        effective_cutoff = max(last_pushed_ms, history_cutoff_ms) if last_pushed_ms == 0 else last_pushed_ms

        # 找新 ≥B 信号 (晚于 effective_cutoff)
        new_signals = [s for s in states
                        if s.entry_time_ms > effective_cutoff
                        and GRADE_RANK.get(s.grade, 0) >= min_r]

        if not new_signals:
            # 首次运行 + push_current: 推送当前活跃持仓
            if push_current and last_pushed_ms == 0:
                # 找当前是否在 long 持仓 (最近一次 ≥B 信号是 long)
                latest_ge_b = [s for s in states if GRADE_RANK.get(s.grade,0) >= min_r]
                if latest_ge_b and latest_ge_b[-1].direction == "long":
                    s = latest_ge_b[-1]
                    slip = (cur_price - s.entry_price) / s.entry_price * 100
                    if not dry_run:
                        send_signal_card(
                            symbol=sym, direction="BUY",
                            grade=s.grade, score=s.score,
                            price=s.entry_price, sector=sector, tf=interval,
                            current_price=cur_price, slippage_pct=slip,
                            reason="📦 当前活跃持仓 (历史信号)",
                            note=f"该标的最后一次 ≥B 入场信号 ({_fmt_dt(s.entry_time_ms)} CST), 系统认为仍持有中.",
                            signal_time_ms=s.entry_time_ms,
                        )
                    state[sym] = {"last_signal_ms": s.entry_time_ms, "last_action": "BUY"}
                    summary_lines.append(f"📦 {sym} {s.grade}({s.score}) ${s.entry_price:.2f} (持仓中, 滑点 {slip:+.1f}%)")
                    pushed_buy += 1
            continue

        # 处理每个新信号 (理论上一次扫描通常只有 1-2 个新的)
        for s in new_signals:
            slip = (cur_price - s.entry_price) / s.entry_price * 100
            direction = "BUY" if s.direction == "long" else "SELL"
            reason = "新信号触发"
            note = ""
            if direction == "BUY" and last_action == "BUY":
                # 异常: long 后又 long (不应该, RF 状态机应该交替)
                reason = "再次 BUY (无视上次 BUY)"
            elif direction == "SELL" and last_action == "BUY":
                reason = "评级反向出场 (≥B short)"
                note = "系统建议平掉之前的 BUY 仓位."
            elif direction == "SELL" and last_action != "BUY":
                # 仅多策略下, 先来一个 short 没意义 (没空可平)
                reason = "评级 SELL 信号 (无 long 仓可平)"
                note = "🟡 仅多策略下此信号不触发交易, 仅作记录."

            if not dry_run:
                send_signal_card(
                    symbol=sym, direction=direction,
                    grade=s.grade, score=s.score,
                    price=s.entry_price, sector=sector, tf=interval,
                    current_price=cur_price, slippage_pct=slip,
                    reason=reason, note=note,
                    signal_time_ms=s.entry_time_ms,
                )
            line = (f"{'🟢' if direction=='BUY' else '🔴'} {sym} {direction} {s.grade}({s.score}) "
                    f"${s.entry_price:.2f} ({_fmt_dt(s.entry_time_ms)} CST)")
            summary_lines.append(line)
            if direction == "BUY":
                pushed_buy += 1
            else:
                pushed_sell += 1

            # 更新 state
            state[sym] = {"last_signal_ms": s.entry_time_ms, "last_action": direction}

    if not dry_run:
        _save_state(state)

    return {
        "pushed_buy": pushed_buy, "pushed_sell": pushed_sell,
        "lines": summary_lines,
    }


def _fmt_dt(ms: int) -> str:
    return (datetime.fromtimestamp(ms / 1000, tz=timezone.utc) + timedelta(hours=8)).strftime("%m-%d %H:%M")
