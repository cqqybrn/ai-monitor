"""Discord Webhook 推送封装 — 仅多 + 评级过滤策略的信号通知"""
from __future__ import annotations
import os
from datetime import datetime, timezone
from typing import Optional
import requests

DISCORD_ENV = "DISCORD_WEBHOOK_URL"


def _get_url(url: Optional[str] = None) -> str:
    u = url or os.getenv(DISCORD_ENV)
    if not u:
        raise RuntimeError(
            f"Discord webhook URL 未设置. 请设置环境变量 {DISCORD_ENV}=https://..."
        )
    return u


def send_text(content: str, url: Optional[str] = None):
    """纯文本消息"""
    r = requests.post(_get_url(url), json={"content": content}, timeout=10)
    r.raise_for_status()
    return r


def send_signal_card(
    symbol: str,
    direction: str,            # "BUY" / "SELL" / "ROTATE_OUT" / "ROTATE_IN"
    grade: str,                # "S" / "A" / "B"
    score: int,
    price: float,
    sector: str,
    tf: str,                   # "4h" / "8h"
    current_price: Optional[float] = None,
    slippage_pct: Optional[float] = None,
    reason: str = "信号触发",
    extra_fields: Optional[list[tuple[str, str]]] = None,
    note: str = "",
    url: Optional[str] = None,
    signal_time_ms: Optional[int] = None,  # 信号实际触发时间 (bar open_time)
):
    """推送结构化的信号卡片"""
    if direction == "BUY":
        emoji = "🟢"; color = 0x2ECC71
    elif direction == "SELL":
        emoji = "🔴"; color = 0xE74C3C
    elif direction == "ROTATE_OUT":
        emoji = "🔄"; color = 0xF39C12
    elif direction == "ROTATE_IN":
        emoji = "⭐"; color = 0x3498DB
    else:
        emoji = "⚪"; color = 0x95A5A6

    if grade in ("S", "A"):
        grade_emoji = "⭐" if grade == "A" else "🔥"
    else:
        grade_emoji = ""

    fields = [
        {"name": "评级", "value": f"**{grade}({score}){grade_emoji}**", "inline": True},
        {"name": "板块", "value": sector, "inline": True},
        {"name": "周期", "value": tf.upper(), "inline": True},
        {"name": "触发价", "value": f"${price:.2f}", "inline": True},
    ]
    if signal_time_ms is not None:
        from datetime import timedelta
        cst_dt = datetime.fromtimestamp(signal_time_ms/1000, tz=timezone.utc) + timedelta(hours=8)
        fields.append({"name": "信号时间", "value": f"`{cst_dt.strftime('%Y-%m-%d %H:%M')} CST`", "inline": False})
    if current_price is not None:
        fields.append({"name": "当前价", "value": f"${current_price:.2f}", "inline": True})
    if slippage_pct is not None:
        if abs(slippage_pct) < 1.5: slip_emoji = "✅"
        elif abs(slippage_pct) < 4: slip_emoji = "🟡"
        else: slip_emoji = "🔴"
        fields.append({"name": "滑点", "value": f"{slip_emoji} {slippage_pct:+.2f}%", "inline": True})
    if extra_fields:
        for name, value in extra_fields:
            fields.append({"name": name, "value": value, "inline": True})

    # embed timestamp 用信号触发时间 (如果有), 否则当前时间
    if signal_time_ms is not None:
        ts = datetime.fromtimestamp(signal_time_ms/1000, tz=timezone.utc).isoformat()
    else:
        ts = datetime.now(timezone.utc).isoformat()
    embed = {
        "title": f"{emoji} {symbol} {direction}",
        "color": color,
        "fields": fields,
        "footer": {"text": f"AI Tech Monitor · {reason}"},
        "timestamp": ts,
    }
    if note:
        embed["description"] = note

    r = requests.post(_get_url(url), json={"embeds": [embed]}, timeout=10)
    r.raise_for_status()
    return r


def send_summary(title: str, lines: list[str], color: int = 0x3498DB,
                  url: Optional[str] = None):
    """每日/每周汇总卡片"""
    embed = {
        "title": title,
        "color": color,
        "description": "\n".join(lines),
        "footer": {"text": "AI Tech Monitor · 汇总"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    r = requests.post(_get_url(url), json={"embeds": [embed]}, timeout=10)
    r.raise_for_status()
    return r
