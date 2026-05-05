"""命令行入口

用法：
    python -m src.cli fetch-crypto             # 拉所有加密合约 K 线
    python -m src.cli fetch-stocks             # 拉所有美股 K 线
    python -m src.cli fetch-all                # 全拉
    python -m src.cli show BTCUSDT 1h          # 查看本地数据末尾 + 指标
"""
from __future__ import annotations
import sys
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
import time
import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from pathlib import Path
from config import (
    CRYPTO_SYMBOLS, STOCK_SYMBOLS, INTERVALS,
    CRYPTO_DIR, STOCK_DIR,
)
ROOT_DIR = Path(__file__).resolve().parent.parent
from src import storage
from src.fetchers import binance_futures, yfinance_us
from src import indicators

app = typer.Typer(help="K 线数据下载与分析")
console = Console()


def _fetch_crypto_one(symbol: str, interval: str, lookback_days: int) -> int:
    """拉单个加密合约 K 线，增量合并到 parquet。返回新增条数。"""
    old = storage.load(CRYPTO_DIR, symbol, interval)
    last_ms = storage.last_open_time_ms(old)
    if last_ms is None:
        start_ms = int(time.time() * 1000) - lookback_days * 86_400_000
    else:
        start_ms = last_ms

    new = binance_futures.fetch_klines(symbol, interval, start_ms)
    merged = storage.merge_incremental(old, new)
    storage.save(merged, CRYPTO_DIR, symbol, interval)
    return len(merged) - len(old)


def _fetch_stock_one(symbol: str, interval: str) -> int:
    old = storage.load(STOCK_DIR, symbol, interval)
    new = yfinance_us.fetch(symbol, interval)
    merged = storage.merge_incremental(old, new)
    storage.save(merged, STOCK_DIR, symbol, interval)
    return len(merged) - len(old)


@app.command("fetch-crypto")
def fetch_crypto(
    lookback_days: int = typer.Option(365, help="首次拉取的回溯天数"),
):
    """下载币安合约 K 线（USD-M 永续）"""
    table = Table(title="加密合约 K 线下载")
    table.add_column("Symbol")
    table.add_column("Interval")
    table.add_column("New Rows", justify="right")
    table.add_column("Status")

    for symbol in CRYPTO_SYMBOLS:
        for interval in INTERVALS:
            try:
                added = _fetch_crypto_one(symbol, interval, lookback_days)
                table.add_row(symbol, interval, str(added), "[green]OK[/green]")
            except Exception as e:
                table.add_row(symbol, interval, "0", f"[red]ERR: {e}[/red]")
    console.print(table)


@app.command("fetch-stocks")
def fetch_stocks():
    """下载美股 K 线（yfinance；2h/4h 由 1h resample）"""
    table = Table(title="美股 K 线下载")
    table.add_column("Symbol")
    table.add_column("Interval")
    table.add_column("New Rows", justify="right")
    table.add_column("Status")

    for symbol in STOCK_SYMBOLS:
        for interval in INTERVALS:
            try:
                added = _fetch_stock_one(symbol, interval)
                table.add_row(symbol, interval, str(added), "[green]OK[/green]")
            except Exception as e:
                table.add_row(symbol, interval, "0", f"[red]ERR: {e}[/red]")
    console.print(table)


@app.command("fetch-all")
def fetch_all(lookback_days: int = typer.Option(365)):
    """加密 + 美股一把梭"""
    fetch_crypto(lookback_days=lookback_days)
    fetch_stocks()


@app.command("backtest")
def backtest(
    symbol: str = typer.Argument("BTCUSDT"),
    enable_short: bool = typer.Option(False, help="启用空单"),
    save_trades: bool = typer.Option(True, help="保存 trade log csv"),
):
    """跑 BTC v11 策略回测 (1H 主图 + 2H/4H 共振)"""
    from src.strategies.btc_v11 import StratParams, build_signal_frame, run_strategy

    df_1h = storage.load(CRYPTO_DIR, symbol, "1h")
    df_2h = storage.load(CRYPTO_DIR, symbol, "2h")
    df_4h = storage.load(CRYPTO_DIR, symbol, "4h")
    if df_1h.empty or df_2h.empty or df_4h.empty:
        console.print(f"[red]缺少数据。先跑: python -m src.cli fetch-crypto --lookback-days 1095[/red]")
        raise typer.Exit(1)

    console.print(f"[cyan]Bars: 1H={len(df_1h)} | 2H={len(df_2h)} | 4H={len(df_4h)}[/cyan]")

    params = StratParams(enable_short=enable_short)
    signals = build_signal_frame(df_1h, df_2h, df_4h, params)
    console.print(f"[cyan]Signal frame: {len(signals)} rows[/cyan]")

    result = run_strategy(signals, params)
    m = result.metrics()

    table = Table(title=f"BTC v11 回测结果 — {symbol} (enable_short={enable_short})")
    table.add_column("Metric")
    table.add_column("Value", justify="right")
    for k, v in m.items():
        if isinstance(v, float):
            table.add_row(k, f"{v:.3f}")
        else:
            table.add_row(k, str(v))
    console.print(table)

    if save_trades:
        td = result.trade_df()
        out_path = ROOT_DIR / f"backtest_{symbol}_v11_{'long_short' if enable_short else 'long_only'}.csv"
        td.to_csv(out_path, index=False)
        eq_path = ROOT_DIR / f"backtest_{symbol}_v11_equity.csv"
        result.equity_df().to_csv(eq_path, index=False)
        console.print(f"[green]Trade log -> {out_path}[/green]")
        console.print(f"[green]Equity curve -> {eq_path}[/green]")


@app.command("watch")
def watch(
    symbol: str = typer.Argument("BTCUSDT"),
    interval: str = typer.Option("4h", help="主趋势周期"),
    rf_period: int = typer.Option(100, help="Range Filter 周期"),
    rf_mult: float = typer.Option(3.0, help="Range Filter 乘数"),
    tail: int = typer.Option(3, help="显示最近 N 个历史信号 (含当前)"),
    use_priors: bool = typer.Option(False, help="启用 Tier 3 历史胜率先验 (walk-forward)"),
):
    """信号监控 — 仿"懂币猫"机器人格式 + 触发时锁定的多TF强度评分"""
    from src.monitor import build_watch_frame, detect_signal_flips, format_signal_card

    base_dir = CRYPTO_DIR if symbol.endswith("USDT") else STOCK_DIR
    main_df = storage.load(base_dir, symbol, interval)
    if main_df.empty:
        console.print(f"[red]无数据。先运行: python -m src.cli fetch-crypto[/red]")
        raise typer.Exit(1)

    extras = {}
    for tf in ("1h", "2h", "1d"):
        if tf == interval:
            continue
        edf = storage.load(base_dir, symbol, tf)
        if not edf.empty:
            extras[tf] = (edf, tf)

    watch_df = build_watch_frame(main_df, interval, extras, rf_period, rf_mult)
    tf_labels = list(extras.keys())
    states = detect_signal_flips(watch_df, tf_labels, use_priors=use_priors)
    if not states:
        console.print("[yellow]近期无翻转信号[/yellow]")
        return

    sector = "加密主流" if symbol.endswith("USDT") else "美股"
    cards = states[-tail:]
    for st in cards:
        card = format_signal_card(symbol, st, interval, sector=sector)
        console.print("─" * 50)
        console.print(card)
    console.print("─" * 50)


@app.command("grade-backtest")
def grade_backtest(
    symbol: str = typer.Argument("BTCUSDT"),
    interval: str = typer.Option("4h"),
    min_grade: str = typer.Option("A", help="只交易此评级以上 (S/A/B/C/D)"),
    long_only: bool = typer.Option(True),
    commission: float = typer.Option(0.001, help="双边手续费"),
    sl_pct: float = typer.Option(0.0, help="止损 % (0=无, 0.025=2.5%)"),
):
    """评级过滤回测 — 只在 RF 信号评级 >= min_grade 时开仓, 验证评级是否真能改善 Calmar"""
    from src.monitor import build_watch_frame, detect_signal_flips

    base_dir = CRYPTO_DIR if symbol.endswith("USDT") else STOCK_DIR
    main_df = storage.load(base_dir, symbol, interval)
    if main_df.empty:
        console.print(f"[red]缺数据 {symbol}[/red]"); raise typer.Exit(1)
    extras = {}
    for tf in ("1h", "2h", "1d"):
        if tf == interval: continue
        edf = storage.load(base_dir, symbol, tf)
        if not edf.empty: extras[tf] = (edf, tf)

    watch_df = build_watch_frame(main_df, interval, extras, 100, 3.0)
    states = detect_signal_flips(watch_df, list(extras.keys()), use_priors=True)
    closed = [s for s in states if s.bars_since_entry >= 1]

    grade_rank = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}

    def _replay(grade_min: str):
        min_r = grade_rank[grade_min]
        equity = 1.0
        peak = 1.0
        max_dd = 0.0
        rets = []
        wins = 0
        gross_pos = 0.0
        gross_neg = 0.0
        for s in closed:
            if grade_rank.get(s.grade, 0) < min_r:
                continue
            if long_only and s.direction != "long":
                continue
            ret = s.cur_unrealized
            if sl_pct > 0 and s.max_adverse <= -sl_pct:
                ret = -sl_pct
            net = (1 + ret) * (1 - commission) ** 2 - 1
            equity *= (1 + net)
            peak = max(peak, equity)
            max_dd = min(max_dd, (equity / peak) - 1)
            rets.append(net)
            if net > 0:
                wins += 1; gross_pos += net
            else:
                gross_neg += -net
        n = len(rets)
        if n == 0:
            return {"n": 0, "ret%": 0, "dd%": 0, "calmar": 0, "wr%": 0, "pf": 0, "expect%": 0}
        years = (closed[-1].entry_time_ms - closed[0].entry_time_ms) / 1000 / 86400 / 365.25
        cagr = equity ** (1 / max(years, 0.01)) - 1
        return {
            "n": n,
            "ret%": (equity - 1) * 100,
            "cagr%": cagr * 100,
            "dd%": max_dd * 100,
            "calmar": (cagr / abs(max_dd)) if max_dd < 0 else float("inf"),
            "wr%": wins / n * 100,
            "pf": (gross_pos / gross_neg) if gross_neg > 0 else float("inf"),
            "expect%": sum(rets) / n * 100,
        }

    table = Table(title=f"{symbol} {interval} 评级过滤回测  (long_only={long_only}, fee={commission*100:.2f}%, SL={sl_pct*100:.1f}%)")
    table.add_column("过滤"); table.add_column("N", justify="right"); table.add_column("ret%", justify="right")
    table.add_column("CAGR%", justify="right"); table.add_column("dd%", justify="right")
    table.add_column("Calmar", justify="right"); table.add_column("WR%", justify="right")
    table.add_column("PF", justify="right"); table.add_column("Exp%/笔", justify="right")
    for g in ["D", "C", "B", "A", "S"]:
        m = _replay(g)
        if m["n"] == 0:
            continue
        label = "全部" if g == "D" else f"≥{g}"
        cal_str = f"{m['calmar']:.2f}" if m['calmar'] != float("inf") else "∞"
        pf_str = f"{m['pf']:.2f}" if m['pf'] != float("inf") else "∞"
        table.add_row(label, str(m["n"]), f"{m['ret%']:+.1f}", f"{m.get('cagr%', 0):+.1f}",
                      f"{m['dd%']:+.1f}", cal_str, f"{m['wr%']:.1f}", pf_str, f"{m['expect%']:+.2f}")
    console.print(table)


@app.command("notify-init")
def notify_init():
    """初始化 .signal_state.json — 静默记录当前状态, 不推送历史信号"""
    from src.router import _get_states_for, _save_state, _load_state, WATCHLIST, GRADE_RANK, MIN_GRADE
    state = _load_state()
    min_r = GRADE_RANK[MIN_GRADE]
    initialized = 0
    for sym, (interval, sector) in WATCHLIST.items():
        states, _, _ = _get_states_for(sym, interval)
        if not states: continue
        # 找最新一个 ≥B 信号 (作为基线)
        latest = None
        for s in reversed(states):
            if GRADE_RANK.get(s.grade, 0) >= min_r:
                latest = s; break
        if latest:
            state[sym] = {
                "last_signal_ms": latest.entry_time_ms,
                "last_action": "BUY" if latest.direction == "long" else "SELL",
            }
            initialized += 1
    _save_state(state)
    console.print(f"[green]✓ 初始化完成: 记录 {initialized} 个标的的当前状态[/green]")
    console.print(f"[cyan]之后 notify-once 只会推送比这个时间点新的信号[/cyan]")


@app.command("notify-replay")
def notify_replay(
    days: int = typer.Option(30, help="复现过去 N 天的所有 ≥B 信号"),
    delay_sec: float = typer.Option(1.5, help="每条之间间隔(避免触发 Discord 限流)"),
    confirm: bool = typer.Option(False, help="不加 --confirm 只显示数量, 不实推"),
):
    """按时间顺序把过去 N 天的所有 ≥B 信号复现推送到 Discord"""
    from datetime import datetime, timezone, timedelta
    import time
    from src.router import _get_states_for, WATCHLIST, GRADE_RANK, MIN_GRADE
    from src.notifier import send_signal_card, send_text

    cutoff_ms = int((datetime.now(tz=timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    min_r = GRADE_RANK[MIN_GRADE]

    # 收集所有过去 N 天的 ≥B 信号
    all_sigs = []
    for sym, (interval, sector) in WATCHLIST.items():
        states, cur_price, cur_time = _get_states_for(sym, interval)
        if not states: continue
        for i, s in enumerate(states):
            if s.entry_time_ms < cutoff_ms: continue
            if GRADE_RANK.get(s.grade, 0) < min_r: continue
            all_sigs.append({
                "sym": sym, "interval": interval, "sector": sector,
                "state": s, "current_price": cur_price,
            })

    all_sigs.sort(key=lambda x: x["state"].entry_time_ms)

    console.print(f"[cyan]找到 {len(all_sigs)} 个 ≥B 信号 (过去 {days} 天)[/cyan]")
    if not confirm:
        console.print(f"[yellow]这是预览模式. 加 --confirm 真实推送到 Discord[/yellow]")
        for i, sig in enumerate(all_sigs, 1):
            s = sig["state"]
            from datetime import timedelta
            dt = (datetime.fromtimestamp(s.entry_time_ms/1000, tz=timezone.utc)+timedelta(hours=8)).strftime("%Y-%m-%d %H:%M")
            console.print(f"  {i:>2}. {dt} | {sig['sym']:<6} {s.direction:<5} {s.grade}({s.score:>2}) ${s.entry_price:>8.2f}")
        return

    # 真实推送
    eta = len(all_sigs) * delay_sec
    console.print(f"[green]开始推送 {len(all_sigs)} 条信号, 预计耗时 {eta:.0f} 秒...[/green]")
    send_text(f"📜 **历史信号复现** — 接下来推送过去 {days} 天的 {len(all_sigs)} 个 ≥B 信号 (按时间顺序)")
    time.sleep(delay_sec)

    for i, sig in enumerate(all_sigs, 1):
        s = sig["state"]
        slip = (sig["current_price"] - s.entry_price) / s.entry_price * 100
        direction = "BUY" if s.direction == "long" else "SELL"
        try:
            send_signal_card(
                symbol=sig["sym"], direction=direction,
                grade=s.grade, score=s.score,
                price=s.entry_price, sector=sig["sector"], tf=sig["interval"],
                current_price=sig["current_price"], slippage_pct=slip,
                reason=f"📜 历史复现 ({i}/{len(all_sigs)})",
                signal_time_ms=s.entry_time_ms,
            )
            console.print(f"  ✓ {i}/{len(all_sigs)} {sig['sym']} {direction} {s.grade}({s.score})")
        except Exception as e:
            console.print(f"  ❌ 推送失败 {sig['sym']}: {e}")
        time.sleep(delay_sec)

    send_text(f"✅ **复现完成** — 共推送 {len(all_sigs)} 条信号")
    console.print(f"[green]✓ 推送完成[/green]")


@app.command("notify-init")
def notify_test():
    """发送一条测试消息到 Discord, 验证 webhook 配置正确"""
    from src.notifier import send_text, send_signal_card
    send_text("🧪 **AI Tech Monitor 测试消息** — webhook 工作正常!")
    send_signal_card(
        symbol="STX-TEST", direction="BUY",
        grade="A", score=79, price=711.32, sector="存储测试", tf="4h",
        current_price=751.00, slippage_pct=5.58,
        reason="测试推送", note="这是一条测试卡片, 用于验证格式.",
    )
    console.print("[green]✓ 测试消息已发送, 去 Discord 频道查收[/green]")


@app.command("notify-once")
def notify_once(
    dry_run: bool = typer.Option(False, help="仅打印不推送"),
    push_current: bool = typer.Option(False, help="首次运行: 同时推送当前活跃持仓"),
):
    """扫一次 watchlist, 推送新发现的 ≥B 信号"""
    from src.router import scan_once, WATCHLIST
    console.print(f"[cyan]扫描 {len(WATCHLIST)} 个标的 ...[/cyan]")
    result = scan_once(dry_run=dry_run, push_current=push_current)
    console.print(f"\n[green]✓ 推送 BUY: {result['pushed_buy']} 条 / SELL: {result['pushed_sell']} 条[/green]")
    if result["lines"]:
        console.print("\n[bold]推送内容:[/bold]")
        for l in result["lines"]:
            console.print(f"  {l}")
    if dry_run:
        console.print("\n[yellow](dry-run 模式, 实际未发送)[/yellow]")


@app.command("notify-daemon")
def notify_daemon(
    interval_min: int = typer.Option(30, help="扫描间隔 (分钟)"),
    push_current_on_start: bool = typer.Option(False, help="启动时推送当前活跃持仓"),
):
    """常驻进程: 每 N 分钟扫一次 + 自动 fetch 最新数据 + 推 Discord"""
    import time
    from src.router import scan_once
    console.print(f"[cyan]🤖 Daemon 启动, 每 {interval_min} 分钟扫描一次...[/cyan]")
    console.print(f"[cyan]   按 Ctrl+C 退出[/cyan]\n")
    first = True
    while True:
        try:
            t0 = time.time()
            console.print(f"[dim][{time.strftime('%Y-%m-%d %H:%M:%S')}] 开始扫描...[/dim]")
            # 1. 增量拉数据
            try:
                from src.fetchers import yfinance_us
                from config import STOCK_SYMBOLS
                from src import storage
                from config import STOCK_DIR
                for sym in WATCHLIST_SYMS:
                    for tf in ("1h", "1d"):
                        try:
                            old = storage.load(STOCK_DIR, sym, tf)
                            new = yfinance_us.fetch(sym, tf)
                            merged = storage.merge_incremental(old, new)
                            storage.save(merged, STOCK_DIR, sym, tf)
                        except Exception:
                            pass
                # 重算 2h/4h/8h (从 1h resample)
                for sym in WATCHLIST_SYMS:
                    base_1h = storage.load(STOCK_DIR, sym, "1h")
                    if base_1h.empty: continue
                    for tf in ("2h", "4h", "8h"):
                        try:
                            new_resampled = yfinance_us.resample_from_1h(base_1h, tf)
                            storage.save(new_resampled, STOCK_DIR, sym, tf)
                        except Exception:
                            pass
            except Exception as e:
                console.print(f"[yellow]⚠️ 数据更新失败: {e}[/yellow]")

            # 2. 扫描信号
            result = scan_once(dry_run=False, push_current=(first and push_current_on_start))
            first = False
            console.print(f"  -> BUY {result['pushed_buy']}, SELL {result['pushed_sell']}, 耗时 {time.time()-t0:.1f}s")

            time.sleep(interval_min * 60)
        except KeyboardInterrupt:
            console.print("\n[red]Daemon 已停止[/red]")
            break
        except Exception as e:
            console.print(f"[red]错误: {e}[/red] -- 30 秒后重试")
            time.sleep(30)


WATCHLIST_SYMS = ["SNDK","DELL","MU","WDC","STX","CRDO","CIEN","LITE","AAOI","COHR","NOK","NBIS","AMD","INTC","TSM","LRCX","AMAT","TXN","ADI","MCHP","SOUN","PLTR","VRT","CEG"]


@app.command("ema-backtest")
def ema_backtest(
    symbol: str = typer.Argument("ZECUSDT"),
    fast_len: int = typer.Option(20),
    slow_len: int = typer.Option(50),
    commission: float = typer.Option(0.001),
):
    """ZEC EMA 4H金叉进 / 2H死叉出 双均线策略回测 (仅多)"""
    from src.strategies.ema_cross import EmaCrossParams, build_signal_frame, run_strategy
    df_2h = storage.load(CRYPTO_DIR, symbol, "2h")
    df_4h = storage.load(CRYPTO_DIR, symbol, "4h")
    if df_2h.empty or df_4h.empty:
        console.print(f"[red]缺数据 {symbol}[/red]"); raise typer.Exit(1)
    params = EmaCrossParams(fast_len=fast_len, slow_len=slow_len, commission=commission)
    sig = build_signal_frame(df_2h, df_4h, params)
    console.print(f"[cyan]{symbol} 2H={len(df_2h)} 4H={len(df_4h)} entry_pulses={int(sig['entry_pulse'].sum())} exit_signals={int(sig['cross_dn_2h'].sum())}[/cyan]")
    result = run_strategy(sig, params)
    m = result.metrics()
    table = Table(title=f"EMA{fast_len}/{slow_len} 4H↑2H↓ — {symbol} (fee={commission*100:.2f}%)")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    for k, v in m.items():
        table.add_row(k, f"{v:.3f}" if isinstance(v, float) else str(v))
    console.print(table)
    out_path = ROOT_DIR / f"ema_{fast_len}_{slow_len}_{symbol}.csv"
    result.trade_df().to_csv(out_path, index=False)
    console.print(f"[green]→ {out_path}[/green]")


@app.command("zec-backtest")
def zec_backtest(
    symbol: str = typer.Argument("ZECUSDT"),
    enable_short: bool = typer.Option(True),
    mc_pct: float = typer.Option(0.15),
    commission: float = typer.Option(0.001),
):
    """ZEC RF 4H 进 / 2H 出 策略回测 (可换标的)"""
    from src.strategies.zec_rf import ZecParams, build_signal_frame, run_strategy

    df_2h = storage.load(CRYPTO_DIR, symbol, "2h")
    df_4h = storage.load(CRYPTO_DIR, symbol, "4h")
    if df_2h.empty or df_4h.empty:
        console.print(f"[red]缺数据 {symbol}[/red]"); raise typer.Exit(1)

    params = ZecParams(enable_short=enable_short, mc_pct=mc_pct, commission=commission)
    sig = build_signal_frame(df_2h, df_4h, params)
    console.print(f"[cyan]{symbol} 2H bars={len(df_2h)} 4H bars={len(df_4h)} sig_frame={len(sig)}[/cyan]")
    result = run_strategy(sig, params)
    m = result.metrics()

    table = Table(title=f"ZEC RF 策略 — {symbol} (enable_short={enable_short}, MC={mc_pct*100:.0f}%, fee={commission*100:.2f}%)")
    table.add_column("Metric"); table.add_column("Value", justify="right")
    for k, v in m.items():
        table.add_row(k, f"{v:.3f}" if isinstance(v, float) else str(v))
    console.print(table)

    td = result.trade_df()
    if not td.empty:
        reasons = td["exit_reason"].value_counts()
        console.print("\n[cyan]出场原因分布:[/cyan]")
        for r, n in reasons.items():
            console.print(f"  {r:25} {n}")

    out_path = ROOT_DIR / f"zec_strat_{symbol}_short{int(enable_short)}.csv"
    td.to_csv(out_path, index=False)
    console.print(f"[green]→ {out_path}[/green]")


@app.command("analyze")
def analyze(
    symbol: str = typer.Argument("BTCUSDT"),
    interval: str = typer.Option("4h", help="主趋势周期"),
    rf_period: int = typer.Option(100),
    rf_mult: float = typer.Option(3.0),
    win_threshold: float = typer.Option(0.02, help="判定为'胜'的最大浮盈阈值 (默认 2%)"),
    use_priors: bool = typer.Option(False, help="启用 Tier 3 历史先验 (walk-forward)"),
):
    """评分判别力分析 — 历史信号按等级 + (T1, T2) 分桶, 看真实胜率/盈亏比"""
    from src.monitor import build_watch_frame, detect_signal_flips, analyze_history

    base_dir = CRYPTO_DIR if symbol.endswith("USDT") else STOCK_DIR
    main_df = storage.load(base_dir, symbol, interval)
    if main_df.empty:
        console.print(f"[red]无数据[/red]")
        raise typer.Exit(1)

    extras = {}
    for tf in ("1h", "2h", "1d"):
        if tf == interval:
            continue
        edf = storage.load(base_dir, symbol, tf)
        if not edf.empty:
            extras[tf] = (edf, tf)

    watch_df = build_watch_frame(main_df, interval, extras, rf_period, rf_mult)
    states = detect_signal_flips(watch_df, list(extras.keys()), use_priors=use_priors)
    mode = "T1+T2+T3 walk-forward" if use_priors else "T1+T2 only"
    console.print(f"[cyan]总信号数 (含进行中): {len(states)}[/cyan]  (mode: {mode})")

    stats = analyze_history(states, win_threshold=win_threshold)

    # 按等级
    t1 = Table(title=f"{symbol} {interval} - 按评分等级分组 (胜=最大浮盈>{win_threshold*100:.1f}%)")
    t1.add_column("等级")
    t1.add_column("N", justify="right")
    t1.add_column("胜率", justify="right")
    t1.add_column("avg 最大浮盈", justify="right")
    t1.add_column("avg 最大回撤", justify="right")
    t1.add_column("avg 最终", justify="right")
    t1.add_column("expectancy", justify="right")
    for g in ["S", "A", "B", "C", "D"]:
        s = stats["by_grade"].get(g, {"n": 0})
        if s.get("n", 0) == 0:
            t1.add_row(g, "0", "—", "—", "—", "—", "—")
            continue
        t1.add_row(g, str(s["n"]),
                   f"{s['win_rate']*100:.1f}%",
                   f"{s['avg_max_fav']*100:+.2f}%",
                   f"{s['avg_max_adv']*100:+.2f}%",
                   f"{s['avg_final']*100:+.2f}%",
                   f"{s['expectancy']*100:+.2f}%")
    console.print(t1)

    # 按 (direction, T1 桶, T2 桶) — 看 Tier 3 prior 是否有判别力
    t2 = Table(title=f"{symbol} {interval} - 按 (方向, T1, T2) 分桶 (≥3 样本)")
    t2.add_column("方向")
    t2.add_column("T1 桶")
    t2.add_column("T2 桶")
    t2.add_column("N", justify="right")
    t2.add_column("胜率", justify="right")
    t2.add_column("avg 最大浮盈", justify="right")
    t2.add_column("avg 最大回撤", justify="right")
    rows = sorted(stats["by_bucket"].items(),
                  key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    for (direction, b1, b2), s in rows:
        if s.get("n", 0) < 3:
            continue
        t2.add_row(direction, b1, b2, str(s["n"]),
                   f"{s['win_rate']*100:.1f}%",
                   f"{s['avg_max_fav']*100:+.2f}%",
                   f"{s['avg_max_adv']*100:+.2f}%")
    console.print(t2)


@app.command("show")
def show(symbol: str, interval: str, tail: int = 10):
    """查看本地某个 symbol+interval 的最近 tail 根 K 线 + 指标"""
    base_dir = CRYPTO_DIR if symbol.endswith("USDT") else STOCK_DIR
    df = storage.load(base_dir, symbol, interval)
    if df.empty:
        console.print(f"[red]没有数据：{symbol} {interval}。先跑 fetch-crypto / fetch-stocks[/red]")
        raise typer.Exit(1)
    df = indicators.add_all(df).tail(tail)
    cols = ["open_time", "open", "high", "low", "close", "ma20", "rsi14", "macd_hist"]
    cols = [c for c in cols if c in df.columns]
    console.print(df[cols].to_string(index=False))


if __name__ == "__main__":
    app()
