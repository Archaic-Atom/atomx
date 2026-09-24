"""Claude-style status segments. 复用用户的状态栏顺序与剩余额度语义。"""

from __future__ import annotations

import math
import re
import time

from rich.text import Text

from .state import Session, clean


def remaining_color(percent: float) -> str:
    """Color the remaining budget. 按剩余比例显示绿、黄、红。"""
    return "#9dc39a" if percent >= 50 else "#e6b776" if percent >= 20 else "#e58282"


def token_label(value: int | None) -> str:
    """Format cumulative tokens. 按用户约定显示整数 K 和一位小数 M。"""
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1000:
        return f"{math.floor(value / 1000 + 0.5)}K"
    return str(value)


def build_status(session: Session | None, limits: dict, width: int,
                 now: float | None = None, fields: list[str] | None = None) -> Text:
    """Build ordered status segments without inventing unavailable quotas.

    构造常驻状态栏；缺失的上下文和额度字段不占位。

    Args:
        session: Current or highlighted session. 当前或选中的会话。
        limits: Server rate-limit snapshot. 服务端额度快照。
        width: Available terminal cells. 可用终端列数。
        now: Unix timestamp for deterministic checks. 可注入时间戳。

    Returns:
        Styled status text, wrapped between segments. 在字段之间换行的文本。
    """
    now = time.time() if now is None else now
    parts = [("time", Text(time.strftime("%H:%M:%S", time.localtime(now)), "#bbb8b0"))]
    parts.append(("tokens", Text("tok:" + token_label(session.total if session else None), "#e6b776")))
    if session:
        context_size = session.usage.get("modelContextWindow")
        # Context is the latest request, not the accumulated conversation bill.
        # 上下文使用最近一轮的计数，不能用累计计费 Token 代替。
        used = session.usage.get("last", {}).get("totalTokens")
        if context_size and isinstance(used, (int, float)):
            remaining = max(0, min(100, 100 * (context_size - used) / context_size))
            parts.append(("context", Text(f"ctx:{remaining:.0f}%", remaining_color(remaining))))
    buckets = limits.get("rateLimitsByLimitId") or {}
    snapshot = buckets.get("codex") or limits.get("rateLimits") or {}
    for key in ("primary", "secondary"):
        window = snapshot.get(key) or {}
        used = window.get("usedPercent")
        minutes = window.get("windowDurationMins")
        if not isinstance(used, (int, float)) or not minutes:
            continue
        label = (f"{minutes // 1440}d" if minutes % 1440 == 0 else
                 f"{minutes // 60}h" if minutes % 60 == 0 else f"{minutes}m")
        remaining = max(0, min(100, 100 - used))
        part = Text(f"{label}:{remaining:.0f}%", remaining_color(remaining))
        resets_at = window.get("resetsAt")
        if isinstance(resets_at, (int, float)):
            seconds = max(0, int(resets_at - now))
            if not seconds:
                countdown = "待刷新"
            elif minutes >= 1440:
                countdown = f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
            else:
                countdown = f"{seconds // 3600}h {(seconds % 3600) // 60}m"
            part.append(" " + countdown, "dim")
        parts.append(("limits", part))
    if session and session.meta.get("model"):
        name = re.sub(r"\s+\([^)]*context[^)]*\)$", "", clean(session.meta["model"]))
        parts.append(("model", Text(name, "#82becb")))
    result = Text()
    row_width = 0
    ordered = [part for field in (fields if fields is not None else ["time", "tokens", "context", "limits", "model"]) for name, part in parts if name == field]
    for part in ordered:
        if row_width and row_width + 3 + part.cell_len > max(width, 20):
            result.append("\n")
            row_width = 0
        elif row_width:
            result.append(" │ ", "#625f59")
            row_width += 3
        result.append(part)
        row_width += part.cell_len
    return result
