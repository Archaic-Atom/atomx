"""Scoped iTerm2 mouse pointer shapes. 限定在 AtomX 运行期间的鼠标形状。"""

from __future__ import annotations

from textual.driver import Driver

ITERM_SHAPES = {
    "default": "arrow",
    "pointer": "hand2",
    "text": "xterm",
}


def pointer_driver(base: type[Driver], terminal_program: str) -> type[Driver]:
    """Own iTerm2 pointer changes only while application mode is active.

    仅在应用模式期间接管 iTerm2 鼠标形状，退出时恢复。

    Args:
        base: Underlying Textual driver class. 底层 Textual 驱动类。
        terminal_program: Terminal identifier. 终端标识。

    Returns:
        Driver class with scoped pointer support. 带鼠标形状管理的驱动类。
    """

    class MousePointerDriver(base):  # type: ignore[valid-type, misc]
        """Send OSC 22 for iTerm2 and restore on exit. 用 OSC 22 设置并恢复鼠标。"""

        def start_application_mode(self) -> None:
            """Start UI and show an arrow by default. 启动界面并默认显示箭头。"""
            super().start_application_mode()
            self._atomx_pointer_active = terminal_program == "iTerm.app"
            self._atomx_pointer_shape = None
            set_pointer(self, "default")

        def stop_application_mode(self) -> None:
            """Restore the terminal's normal text pointer. 退出时恢复终端指针。"""
            try:
                if getattr(self, "_atomx_pointer_active", False):
                    self._atomx_pointer_active = False
                    self.write("\x1b]22;xterm\x1b\\")
                    self.flush()
            finally:
                super().stop_application_mode()

    return MousePointerDriver


def set_pointer(driver: Driver | None, shape: str) -> None:
    """Write one changed pointer shape to an owned iTerm2 terminal.

    仅在形状变化时向已接管的 iTerm2 终端写入控制序列。

    Args:
        driver: Active Textual driver. 当前 Textual 驱动。
        shape: One of default, pointer or text. 箭头、手形或文本指针。
    """
    if driver is None or not getattr(driver, "_atomx_pointer_active", False):
        return
    if shape == getattr(driver, "_atomx_pointer_shape", None):
        return
    driver.write(f"\x1b]22;{ITERM_SHAPES[shape]}\x1b\\")
    driver.flush()
    setattr(driver, "_atomx_pointer_shape", shape)
