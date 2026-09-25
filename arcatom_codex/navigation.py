"""Navigation. 工作台分区行为。"""

from __future__ import annotations

import time

from textual.widgets import Static

from .access import AppActions
from .i18n import tr
from .widgets import Composer, TranscriptScroll


class Navigation(AppActions):
    """Workspace navigation. 工作台对应分区操作。"""

    def update_navigation_hint(self) -> None:
        """Show the active keyboard mode. 明确编辑、浏览或输入框选择状态。"""
        if self.workspace.command_matches:
            return
        composer = self.workspace.query_one(Composer)
        if self.workspace.current and (
            self.workspace.store.get(self.workspace.current).active_turn
            or self.workspace.current in self.workspace.sending
        ):
            hint = tr("Esc 停止任务 · Ctrl+C 复制 · Ctrl+T 代理与进程")
        elif not composer.read_only:
            hint = tr(
                "编辑 · Esc 浏览 · Ctrl+J 换行 · F3 复制 · F5 回答 · F7 图片 · F2 设置"
            )
        elif composer.has_focus:
            hint = tr(
                "输入框已选中 · 直接输入 / Enter 编辑 · ↑ 浏览 · Esc / ← 首页"
            )
        else:
            hint = tr(
                "浏览 · ↑↓ 滚动 · ↑↑ 顶部 · ↓↓ 底部 · ↓ 回输入框 · Esc / ← 首页"
            )
        self.workspace.query_one("#chat-hint", Static).update(hint)

    def focus_composer(self, edit: bool) -> None:
        """Selecting the input never sends a draft. 选中输入框不会误发草稿。"""
        composer = self.workspace.query_one(Composer)
        composer.read_only = not edit
        composer.focus()
        self.workspace.arrow_gesture.reset()
        self.workspace.update_navigation_hint()

    def leave_composer(self) -> None:
        """Enter transcript browsing while preserving input. 保留草稿并进入日志浏览。"""
        self.workspace.hide_commands()
        self.workspace.query_one(Composer).read_only = True
        self.workspace.query_one("#transcript-scroll").focus()
        self.workspace.arrow_gesture.reset()
        self.workspace.update_navigation_hint()

    def browse_transcript(
        self, key: str, timestamp: float | None = None
    ) -> None:
        """Browse with arrows; a quick repeated arrow jumps to either end.

        方向键浏览，快速连按同向键跳到端点。
        """
        scroll = self.workspace.query_one(
            "#transcript-scroll", TranscriptScroll
        )
        if key in ("up", "home") and scroll.scroll_y == 0:
            self.workspace.load_older_history()
        if key in ("down", "pagedown") and scroll.is_vertical_scroll_end:
            self.workspace.focus_composer(edit=False)
            return
        self.workspace.query_one(Composer).read_only = True
        scroll.focus()
        repeated = self.workspace.arrow_gesture.press(
            key, time.monotonic() if timestamp is None else timestamp
        )
        if repeated or key in ("home", "end"):
            if key in ("up", "home"):
                scroll.scroll_home(animate=False, immediate=True)
            else:
                scroll.scroll_end(animate=False, immediate=True)
            self.workspace.update_navigation_hint()
            return
        step = (
            max(1, scroll.size.height - 2)
            if key in ("pageup", "pagedown")
            else 3
        )
        scroll.scroll_relative(
            y=-step if key in ("up", "pageup") else step,
            animate=False,
            immediate=True,
        )
        self.workspace.update_navigation_hint()
