"""Terminal keyboard ownership and timed navigation. 终端按键协议与导航计时。"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass

from textual import events
from textual.driver import Driver

KITTY_PUSH = re.compile(r"\x1b\[>[0-9]+u")


def keyboard_driver(base: type[Driver], enhanced: bool) -> type[Driver]:
    """Leave the terminal's keyboard mode alone unless opted in.

    Textual versions may push different protocol flag combinations.
    Suppress both push and pop: a lone pop would corrupt the parent's stack.
    默认不改变终端键盘协议；启用和退出必须成对处理，避免破坏父程序状态。
    """
    if enhanced:
        return base

    class StandardKeyboardDriver(base):  # type: ignore[valid-type, misc]
        """Filter only Textual's protocol ownership writes. 仅过滤协议切换。"""

        def write(self, data: str) -> None:
            """Preserve display output and all other modes. 保留显示与其他模式。"""
            if data != "\x1b[<u" and not KITTY_PUSH.fullmatch(data):
                super().write(data)

    return StandardKeyboardDriver


def normalize_keypad_key(event: events.Key) -> events.Key:
    """Supply the missing character for an unmodified keypad decimal key.

    为无修饰键的小键盘小数点补上字符，保留 Delete 和组合键的原有语义。

    Args:
        event: Parsed terminal key event. 终端解析后的按键事件。

    Returns:
        Printable decimal or the unchanged event. 可输入的小数点或原事件。
    """
    if event.key == "decimal" and event.character is None:
        return events.Key("full_stop", ".")
    return event


def reserved_navigation(key: str, platform: str = sys.platform) -> bool:
    """Avoid interpreting desktop navigation as application input.

    This cannot replay a shortcut already intercepted by a terminal profile.
    不把系统组合方向键当作应用导航；终端已截获的快捷键无法由应用转交系统。
    """
    *modifiers, arrow = key.split("+")
    if arrow not in ("left", "right", "up", "down"):
        return False
    if "super" in modifiers or "meta" in modifiers:
        return True
    if platform == "darwin" and "ctrl" in modifiers:
        return True
    return "ctrl" in modifiers and "alt" in modifiers


@dataclass
class ArrowGesture:
    """Pair arrow presses by input time, not render time. 按输入时间识别双击。"""

    key: str | None = None
    timestamp: float = 0.0

    def reset(self) -> None:
        """Forget pending presses when focus changes. 焦点切换后清空按键序列。"""
        self.key = None

    def press(self, key: str, timestamp: float) -> bool:
        """Return whether two matching arrows arrived within 350 ms.

        A consumed pair cannot form a third overlapping gesture.
        双击限时 350 毫秒；第三次按键开始新的序列。
        """
        repeated = (
            key in ("up", "down")
            and self.key == key
            and 0 <= timestamp - self.timestamp <= 0.35
        )
        self.key = None if repeated else key
        self.timestamp = timestamp
        return repeated
