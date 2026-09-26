"""Live terminal titles with scoped ownership. 动态终端标题与成对恢复。"""

from __future__ import annotations

from textual.driver import Driver

from .core.state import Session, clean
from .i18n import tr


def title_driver(base: type[Driver]) -> type[Driver]:
    """Save and restore titles on every terminal handoff. 接管时保存并恢复标题。"""

    class WindowTitleDriver(base):  # type: ignore[valid-type, misc]
        """Own the title only while application mode is active. 仅运行期间持有标题。"""

        def start_application_mode(self) -> None:
            """Start display mode before saving the title. 启动显示后保存原标题。"""
            super().start_application_mode()
            self.write("\x1b[22;0t")
            self._atomx_title_active = True
            self._atomx_last_title = None
            self.flush()

        def stop_application_mode(self) -> None:
            """Restore before handing the terminal back. 交还终端前恢复标题。"""
            try:
                if getattr(self, "_atomx_title_active", False):
                    self._atomx_title_active = False
                    self.write("\x1b[23;0t")
                    self.flush()
            finally:
                super().stop_application_mode()

    return WindowTitleDriver


def write_title(driver: Driver | None, title: str) -> None:
    """Write changed titles only to an owned real terminal. 仅更新已接管终端的标题。"""
    if driver is None or not getattr(driver, "_atomx_title_active", False):
        return
    if title != getattr(driver, "_atomx_last_title", None):
        # C0/C1 characters cannot terminate OSC or inject terminal commands.
        # 剔除控制字符，避免会话名称终止 OSC 或注入终端指令。
        safe = " ".join(clean(title).split())
        safe = "".join(c for c in safe if not 127 <= ord(c) <= 159)[:220]
        driver.write(f"\x1b]0;{safe}\x07")
        driver.flush()
        setattr(driver, "_atomx_last_title", title)


def build_title(
    session: Session | None,
    *,
    ready: bool,
    sending: bool,
    mode: str,
    tick: int,
    working: int = 0,
    waiting: int = 0,
) -> str:
    """Describe activity independently of transcript redraws. 独立于日志重绘显示状态。"""
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[tick % 10]
    if not ready:
        state = "○ " + tr("未连接")
    elif session is None:
        if waiting:
            state = "! " + tr("等待确认 / 输入") + f" ({waiting})"
        elif working:
            state = spinner + " " + tr("运行中") + f" ({working})"
        else:
            state = "○ " + tr("会话列表")
    else:
        flags = session.meta.get("status", {}).get("activeFlags", [])
        if session.pending_requests or any(
            f in flags for f in ("waitingOnApproval", "waitingOnUserInput")
        ):
            state = "! " + tr("等待确认 / 输入")
        elif sending or session.section == "working":
            state = spinner + " " + tr("运行中")
        elif session.last_turn_status == "failed":
            state = "! " + tr("异常")
        elif session.last_turn_status == "interrupted":
            state = "■ " + tr("已停止")
        elif session.last_turn_status == "completed":
            state = "✓ " + tr("已完成")
        else:
            state = "○ " + tr("等待输入")
    parts = [state]
    if session and mode != "app":
        if mode == "model":
            parts.append(session.meta.get("model") or "Codex")
        parts.append(session.title[:100])
    parts.append("AtomX")
    return " · ".join(parts)
