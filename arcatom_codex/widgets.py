"""Keyboard-focused workspace controls. 终端输入与会话列表控件。"""

from __future__ import annotations

import asyncio
import webbrowser
from typing import cast
from urllib.parse import urlsplit

from rich.console import RenderableType
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from textual import events
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.content import Content
from textual.message import Message
from textual.screen import Screen
from textual.selection import Selection
from textual.strip import Strip
from textual.widget import Widget
from textual.widgets import Button, Input, OptionList, Static, TextArea

from .access import WorkspaceAccess
from .i18n import tr
from .terminal.mouse_pointer import set_pointer


class Composer(WorkspaceAccess, TextArea):
    """Edit multiline drafts and recall per-session inputs. 编辑多行草稿并回忆会话输入。"""

    BINDINGS = [
        Binding("enter", "submit", show=False),
        Binding("shift+enter,ctrl+j", "newline", show=False),
    ]

    @property
    def gutter_width(self) -> int:
        """Reserve space for the visual prompt. 为提示符预留空间，不计入输入内容。"""
        return 2

    def render_line(self, y: int) -> Strip:
        """Keep a prompt beside the first visible input row.

        在首个可见输入行旁显示提示符，保留原生光标、选区和换行坐标。
        """
        content = super().render_line(y)
        prompt = Strip(
            [
                Segment(
                    "> " if y == 0 else "  ",
                    Style(color=self.workspace.palette.accent),
                )
            ],
            cell_length=self.gutter_width,
        )
        return Strip.join([prompt, content]).crop(0, self.content_size.width)

    class Submitted(Message):
        """Signal an explicit submit action. 表示用户主动提交。"""

        pass

    class Back(Message):
        """Request navigation to the home list. 请求返回首页列表。"""

        pass

    def reset_history(self) -> None:
        """Forget the history cursor when switching sessions. 切换会话时重置历史位置。"""
        self.history_index: int | None = None
        self.history_draft = ""

    def recall(self, direction: int) -> bool:
        """Recall per-session prompts while preserving unsent edits.

        历史输入不丢草稿。
        """
        if not self.workspace.current:
            return False
        entries = [
            "\n".join(
                c.get("text", "")
                for c in i.get("content", [])
                if c.get("type") == "text"
            )
            for i in self.workspace.store.get(
                self.workspace.current
            ).items.values()
            if i.get("type") == "userMessage"
        ]
        entries = [text for text in entries if text]
        index = getattr(self, "history_index", None)
        if index is None:
            if direction > 0 or not entries:
                return False
            self.history_draft = self.text
            index = len(entries)
        index = max(0, min(len(entries), index + direction))
        self.history_index = None if index == len(entries) else index
        self.load_text(
            self.history_draft if index == len(entries) else entries[index]
        )
        self.move_cursor(self.document.end)
        return True

    async def _on_key(self, event: events.Key) -> None:
        """Route input before the widget applies its default bindings.

        先处理应用按键。
        """
        if self.read_only and event.is_printable:
            self.workspace.focus_composer(edit=True)
        if self.read_only:
            if event.key == "enter":
                self.workspace.focus_composer(edit=True)
            elif event.key == "left":
                self.workspace.show_home()
            elif event.key in ("up", "down", "pageup", "pagedown"):
                self.workspace.browse_transcript(event.key, event.time)
            else:
                await super()._on_key(event)
                return
            event.stop()
            event.prevent_default()
            return
        if self.workspace.command_key(event.key, self):
            event.stop()
            event.prevent_default()
            return
        if event.key in ("up", "down") and (
            getattr(self, "history_index", None) is not None
            or (event.key == "up" and self.cursor_location[0] == 0)
        ):
            if self.recall(-1 if event.key == "up" else 1):
                event.stop()
                event.prevent_default()
                return
        if event.key in ("pageup", "pagedown"):
            event.stop()
            event.prevent_default()
            self.workspace.browse_transcript(event.key, event.time)
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
        else:
            await super()._on_key(event)

    def action_submit(self) -> None:
        """Submit only in editing mode; otherwise enter editing.

        编辑时提交，否则进入编辑。
        """
        if self.read_only:
            self.workspace.focus_composer(edit=True)
        else:
            self.post_message(self.Submitted())

    def on_click(self) -> None:
        """Select the composer for editing. 单击后进入编辑模式。"""
        self.workspace.focus_composer(edit=True)

    def action_newline(self) -> None:
        """Insert a newline and move the cursor before resizing.

        换行后移动光标并调整高度。
        """
        if self.read_only:
            self.workspace.focus_composer(edit=True)
        self.replace("\n", *self.selection, maintain_selection_offset=False)
        self.call_after_refresh(self.fit_height)

    def on_resize(self) -> None:
        """Schedule layout-dependent refresh after dimensions settle.

        布局稳定后刷新。
        """
        self.call_after_refresh(self.fit_height)

    def fit_height(self) -> None:
        """Grow to fit wrapped input within available space. 按可用空间展开输入。"""
        if (
            not self.is_mounted
            or not isinstance(self.parent, Widget)
            or not self.parent.display
        ):
            return
        reserved = sum(
            widget.outer_size.height
            for widget in self.parent.children
            if widget is not self
            and widget.id != "transcript-scroll"
            and widget.display
        )
        maximum = max(3, self.parent.content_size.height - reserved - 3)
        height = min(maximum, max(3, self.wrapped_document.height + 2))
        if self.region.height != height:
            scroll = self.workspace.query_one(
                "#transcript-scroll", VerticalScroll
            )
            follow = (
                self.workspace.view_preferences.get("follow_output", True)
                and not scroll.has_focus
                and scroll.is_vertical_scroll_end
            )
            session_id = self.workspace.current
            self.styles.height = height

            # Restore the bottom after the new viewport is laid out. 布局后保持日志底部。
            def settle_layout() -> None:
                """Preserve the visible transcript after composer resizing.

                输入高度改变后保持日志位置。
                """
                if self.workspace.current != session_id:
                    return
                self.scroll_cursor_visible()
                if follow and not scroll.has_focus:
                    scroll.scroll_end(animate=False, immediate=True)
                scroll.refresh()

            self.call_after_refresh(settle_layout)

    def action_cursor_left(self, select: bool = False) -> None:
        """Return home only from an empty composer. 仅在空输入时返回首页。"""
        if self.text == "":
            self.post_message(self.Back())
        else:
            super().action_cursor_left(select)


class SessionSearch(WorkspaceAccess, Input):
    """Wake home search without consuming the first character. 唤醒首页搜索且保留首字。"""

    async def _on_key(self, event: events.Key) -> None:
        """Route input before the widget applies its default bindings.

        先处理应用按键。
        """
        if self.workspace.command_key(event.key, self):
            event.stop()
            event.prevent_default()
            return
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            self.workspace.move_home_focus(-1 if event.key == "up" else 1)
        else:
            await super()._on_key(event)


class HomeButton(WorkspaceAccess, Button, can_focus=False):
    """Expose mouse actions without adding keyboard focus stops.

    支持鼠标操作且不占键盘焦点。
    """

    BINDINGS = [
        Binding("up", "home_focus(-1)", show=False),
        Binding("down", "home_focus(1)", show=False),
    ]

    def action_home_focus(self, direction: int) -> None:
        """Move selection within the home session rows. 移动首页会话选择。"""
        self.workspace.move_home_focus(direction)


class SessionList(WorkspaceAccess, OptionList):
    """The highlighted row belongs only to the focused list.

    仅在列表有焦点时显示当前高亮行。
    """

    def on_resize(self) -> None:
        # A hidden list has zero width; rebuild columns after layout. 布局后重建列宽。
        """Schedule layout-dependent refresh after dimensions settle.

        布局稳定后刷新。
        """
        self.call_after_refresh(self.refresh_columns)

    def on_show(self) -> None:
        """Rebuild columns when the hidden list becomes visible. 列表重新显示后重建列宽。"""
        self.call_after_refresh(self.refresh_columns)

    def refresh_columns(self) -> None:
        """Rebuild visible home rows using the current width. 按当前可见宽度重建条目。"""
        if (
            self.is_mounted
            and self.is_on_screen
            and self.workspace.current is None
        ):
            self.workspace.paint_sessions()

    def selectable_indices(self) -> list[int]:
        """Return row indices excluding section headings. 排除分区标题返回可选行。"""
        return [
            i
            for i in range(self.option_count)
            if not self.get_option_at_index(i).disabled
        ]

    def on_focus(self) -> None:
        """Restore the last selected session when focus returns. 焦点返回后恢复选择。"""
        if self.highlighted is None:
            indices = self.selectable_indices()
            self.highlighted = next(
                (
                    index
                    for index in indices
                    if self.get_option_at_index(index).id
                    == self.workspace.home_selection
                ),
                next(iter(indices), None),
            )

    def on_blur(self) -> None:
        """Clear transient selection or editing state on blur. 失焦后清除临时状态。"""
        self.workspace.delete_confirmation = None
        if (
            self.highlighted is not None
            and self.highlighted < self.option_count
        ):
            self.workspace.home_selection = self.get_option_at_index(
                self.highlighted
            ).id
        self.highlighted = None

    def move(self, direction: int) -> None:
        """Wrap selection across enabled session rows. 在可选会话之间循环移动。"""
        indices = self.selectable_indices()
        if not indices:
            return
        index = (
            indices.index(self.highlighted)
            if self.highlighted in indices
            else -1
        )
        self.highlighted = indices[(index + direction) % len(indices)]

    def action_cursor_up(self) -> None:
        """Select the preceding session. 选择上一条会话。"""
        self.move(-1)

    def action_cursor_down(self) -> None:
        """Select the following session. 选择下一条会话。"""
        self.move(1)


class SelectableTranscript(WorkspaceAccess, Static):
    """Preserve Rich formatting while exposing real text selection coordinates.

    保留格式并提供准确的文字选择坐标。
    """

    def render(self) -> Content:
        """Expose cached Rich text with selectable coordinates. 提供带选择坐标的缓存文本。"""
        width = max(1, self.content_size.width)
        key = (id(self.content), width)
        if getattr(self, "_selection_cache_key", None) != key:
            text = Text()
            ignored: set[int] = set()
            for segment in self.workspace.console.render(
                cast(RenderableType, self.content),
                self.workspace.console.options.update(
                    width=width, highlight=False
                ),
            ):
                if not segment.control:
                    if segment.style and segment.style.meta.get(
                        "atomx_copy_padding"
                    ):
                        ignored.update(
                            range(len(text), len(text) + len(segment.text))
                        )
                    text.append(segment.text, segment.style)
            self._selection_content = Content.from_rich_text(
                text, console=self.workspace.console
            )
            self._selection_cache_key = key
            self._copy_ignored = ignored
        return self._selection_content

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Copy selected text without terminal fill or decorative code margins.

        选区坐标仍对应屏幕文字，只在复制时去掉行尾补齐及已标记的代码边距。
        保留正文换行、代码缩进和表格内部用于对齐的空格。
        """
        content = self.render()
        lines = []
        offset = 0
        for y, line in enumerate(content.plain.splitlines()):
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                end = len(line) if end == -1 else min(end, len(line))
                end = min(end, len(line.rstrip(" \t")))
                selected = "".join(
                    line[x]
                    for x in range(max(0, start), end)
                    if offset + x not in self._copy_ignored
                )
                if (
                    selection.end is not None
                    and y == selection.end.y
                    and selected.strip(" \t")
                ):
                    selected = selected.rstrip(" \t")
                lines.append(selected)
            offset += len(line) + 1
        return "\n".join(lines), "\n"

    def on_mouse_down(self, event: events.MouseDown) -> None:
        """Remember an action press so drags cannot trigger clicks.

        记录按下位置，防止拖选文字时误触链接或命令。
        """
        self._action_mouse_down = (
            (event.screen_x, event.screen_y) if event.button == 1 else None
        )
        if (
            event.button == 1
            and not event.style.link
            and not event.style.meta.get("atomx_activity_id")
        ):
            set_pointer(self.workspace._driver, "text")

    def on_mouse_up(self, event: events.MouseUp) -> None:
        """Return to the hover shape after selection. 选完文字后恢复悬停形状。"""
        self.workspace.update_mouse_pointer(event, selecting=False)

    async def on_click(self, event: events.Click) -> None:
        """Open a link or toggle one command only on a deliberate click.

        明确单击时打开链接或展开命令，拖选时不触发。
        """
        down = getattr(self, "_action_mouse_down", None)
        self._action_mouse_down = None
        if (
            event.button != 1
            or event.chain != 1
            or down != (event.screen_x, event.screen_y)
            or self.screen.selections
        ):
            return
        activity_id = event.style.meta.get("atomx_activity_id")
        if activity_id and self.screen is self.workspace.main_screen:
            event.stop()
            self.workspace.toggle_inline_activity(str(activity_id))
            return
        url = event.style.link
        if not url:
            return
        try:
            parsed = urlsplit(url)
        except ValueError:
            return
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            return
        event.stop()
        try:
            opened = await asyncio.to_thread(webbrowser.open, url)
        except (OSError, ValueError):
            opened = False
        if not opened:
            self.notify(
                tr("浏览器未能自动打开，请复制链接手动打开。"),
                severity="warning",
            )

    def on_resize(self) -> None:
        """Schedule layout-dependent refresh after dimensions settle.

        布局稳定后刷新。
        """
        self.refresh(layout=True)


class TranscriptScroll(WorkspaceAccess, VerticalScroll):
    """Arrow navigation returns naturally to the composer. 方向键浏览后返回输入。"""

    @property
    def is_vertical_scroll_end(self) -> bool:
        """Treat non-overflowing anchored content as already at the bottom.

        短内容贴底时 Textual 可能产生负偏移；没有溢出就已经在底部。
        """
        return self.max_scroll_y == 0 or super().is_vertical_scroll_end

    def on_mount(self) -> None:
        """Use layout anchoring and pause during selection. 布局持续贴底，选区时暂停。"""
        self.screen.text_selection_started_signal.subscribe(
            self, self.pause_for_selection
        )
        self.sync_follow()

    def sync_follow(self, *, restart: bool = False) -> None:
        """Keep the anchor across layout passes, respecting deliberate browsing.

        跨多次布局保持贴底；主动浏览历史、选择文字及关闭跟随时暂停。
        """
        enabled = self.workspace.view_preferences.get("follow_output", True)
        if not enabled or self.screen.selections:
            self.anchor(False)
        elif restart or (not self.is_anchored and self.is_vertical_scroll_end):
            self.anchor()

    def pause_for_selection(self, screen: Screen) -> None:
        """Stop moving text before a drag begins. 拖选开始前停止移动正文。"""
        self.anchor(False)

    async def on_key(self, event: events.Key) -> None:
        """Handle transcript browsing without changing a draft. 浏览日志且保留输入草稿。"""
        if event.key == "left":
            event.stop()
            event.prevent_default()
            self.workspace.show_home()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            self.workspace.focus_composer(edit=True)
        elif event.key in ("up", "down", "pageup", "pagedown", "home", "end"):
            event.stop()
            event.prevent_default()
            self.workspace.browse_transcript(event.key, event.time)
