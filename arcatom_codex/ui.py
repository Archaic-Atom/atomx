"""AtomX application lifecycle and backend events. 应用生命周期与后端事件。"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path
from typing import Any

from rich.console import Group
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    OptionList,
    Static,
    TextArea,
)
from textual.widgets.option_list import Option

from .appearance import brand, palette_for
from .backend_actions import BackendActions
from .clipboard import copy_text, import_image, read_clipboard
from .command_actions import CommandActions
from .commands import BY_NAME, Command, matches
from .demo import DemoClient
from .dialogs import Approval as Approval
from .dialogs import Detail as Detail
from .dialogs import NewSession as NewSession
from .dialogs import Question as Question
from .history_actions import HistoryActions
from .home_actions import HomeActions
from .i18n import tr
from .image_widgets import MediaTranscript
from .keyboard import ArrowGesture, keyboard_driver, reserved_navigation
from .navigation import Navigation
from .personal import bridge_instructions, discover_skills
from .pickers import Prompt
from .preferences import read_preferences
from .rendering import MessageMarkdown as MessageMarkdown
from .rendering import command_summary as command_summary
from .rendering import pretty as pretty
from .response_actions import ResponseActions
from .rpc import CodexClient, RpcError
from .settings import Settings
from .state import Store, clean, number, rollout_usage
from .turn_actions import TurnActions
from .view_actions import ViewActions
from .widgets import Composer as Composer
from .widgets import HomeButton as HomeButton
from .widgets import SelectableTranscript as SelectableTranscript
from .widgets import SessionList as SessionList
from .widgets import SessionSearch as SessionSearch
from .widgets import TranscriptScroll as TranscriptScroll
from .window_title import build_title, title_driver, write_title


class AtomXApp(
    ResponseActions,
    CommandActions,
    Navigation,
    HomeActions,
    HistoryActions,
    ViewActions,
    BackendActions,
    TurnActions,
    App,
):
    """Coordinate terminal screens and their asynchronous backend.

    协调终端界面与异步后端。
    """

    TITLE = "AtomX"
    CSS_PATH = "style.tcss"
    BINDINGS = [
        Binding("ctrl+n", "new_session", tr("新会话"), priority=True),
        Binding("f2", "settings", tr("设置"), priority=True),
        Binding("ctrl+l,f6", "focus_input", tr("回到输入框"), priority=True),
        Binding(
            "f3,ctrl+shift+c,super+c",
            "copy_selection",
            tr("复制"),
            priority=True,
        ),
        Binding(
            "ctrl+v", "paste_clipboard", tr("粘贴文字 / 图片"), priority=True
        ),
        Binding("f4", "attach_image", tr("添加图片"), priority=True),
        Binding("f5", "copy_response", tr("复制回答"), priority=True),
        Binding("f7", "view_images", tr("查看图片"), priority=True),
        Binding("f8", "attachments", tr("管理图片"), priority=True),
        Binding("ctrl+u", "usage", tr("用量"), priority=True),
        Binding("ctrl+t", "activity", tr("代理与进程"), priority=True),
        Binding("ctrl+r", "refresh_sessions", tr("刷新"), priority=True),
        Binding("ctrl+q", "request_quit", tr("退出"), priority=True),
        Binding("ctrl+c", "copy_selection", tr("复制"), priority=True),
        Binding("ctrl+x", "delete_session", tr("删除会话"), priority=True),
        Binding("escape", "escape", tr("返回"), priority=True),
    ]

    def __init__(
        self,
        cwd: str,
        client: CodexClient | DemoClient | None = None,
        demo: bool = False,
        enhanced_keyboard: bool | None = None,
    ) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        preferences = {} if demo else read_preferences()
        enhanced = (
            preferences.get("enhanced_keyboard", False)
            if enhanced_keyboard is None
            else enhanced_keyboard
        )
        super().__init__(
            ansi_color=True,
            driver_class=title_driver(
                keyboard_driver(self.get_driver_class(), enhanced)
            ),
        )
        self.cwd, self.demo = cwd, demo
        self.client = client or CodexClient(cwd=cwd)
        self.store = Store()
        self.current: str | None = None
        self.ready = False
        self.detail_open = False
        self.sending: set[str] = set()
        self.stop_requested: set[str] = set()
        self.interrupting: set[str] = set()
        self.home_selection: str | None = None
        self.marquee_tid: str | None = None
        self.marquee_step = 0
        self.last_revision = -1
        self.requests: asyncio.Queue[dict] = asyncio.Queue()
        self.activity_targets: dict[str, tuple[str, Any]] = {}
        self.main_screen: Screen | None = None
        self.connection_text = tr("正在连接本机 Codex…")
        self.poll_timer: Timer | None = None
        self.account_timer: Timer | None = None
        self.account_loading = False
        self.deleting: set[str] = set()
        self.delete_confirmation: tuple[str, float] | None = None
        self.last_status_second = -1
        self.transcript_cache: dict[str, tuple] = {}
        self.transcript_signature: tuple | None = None
        self.activity_signature: tuple | None = None
        self.personal_skills = [] if demo else discover_skills()
        self.personal_instructions = (
            "" if demo else bridge_instructions(self.personal_skills)
        )
        self.resume_locks: dict[str, asyncio.Lock] = {}
        self.command_busy = False
        self.home_command_starting = False
        self.command_matches: list[Command] = []
        self.command_dismissed: str | None = None
        self.raw_transcript = False
        self.view_preferences = {} if demo else read_preferences()
        self.language = (
            "zh" if self.view_preferences.get("language") == "zh" else "en"
        )
        self.status_fields = ["time", "tokens", "context", "limits", "model"]
        fields = self.view_preferences.get("status_fields")
        if (
            isinstance(fields, list)
            and fields
            and all(f in self.status_fields for f in fields)
        ):
            self.status_fields = list(dict.fromkeys(fields))
        from pygments.styles import get_all_styles

        theme = self.view_preferences.get("code_theme", "monokai")
        self.code_theme = (
            theme
            if isinstance(theme, str) and theme in set(get_all_styles())
            else "monokai"
        )
        self.native_active = False
        self.pasting = False
        self.creating = False
        self.arrow_gesture = ArrowGesture()
        self.attachment_cache = (
            Path.home() / ".cache" / "arcatom" / "attachments"
        )
        self.appearance_preferences = dict(self.view_preferences)
        self.palette = palette_for(self.view_preferences)
        self.apply_appearance(self.view_preferences)

    async def on_event(self, event: events.Event) -> None:
        """Route typing before list shortcuts can consume it. 输入首字不被列表吞掉。"""
        if isinstance(event, events.Key) and reserved_navigation(event.key):
            event.stop()
            event.prevent_default()
            return
        if (
            isinstance(event, events.InputEvent)
            and not event.is_forwarded
            and (
                isinstance(event, events.Key)
                and event.key != "ctrl+x"
                or isinstance(event, (events.MouseDown, events.Paste))
            )
        ):
            self.delete_confirmation = None
        typing = (
            isinstance(event, events.Key)
            and event.is_printable
            or isinstance(event, events.Paste)
        )
        if (
            typing
            and not event.is_forwarded
            and self.main_screen is not None
            and self.screen is self.main_screen
        ):
            if self.current:
                self.focus_composer(edit=True)
                self.screen.set_focus(self.query_one(Composer))
            else:
                self.screen.set_focus(self.query_one("#search"))
        await super().on_event(event)

    def apply_appearance(self, preferences: dict) -> None:
        """Update widgets and cached Rich messages together. 同步 CSS 和历史消息。"""
        self.appearance_preferences = dict(preferences)
        self.palette = palette_for(preferences)
        theme = self.palette.theme()
        for variable in ("arc-background", "arc-surface", "arc-foreground"):
            theme.variables[variable] = "ansi_default"
        # Keep terminal text readable with light and dark profiles. 文字跟随终端配色。
        self.palette = replace(self.palette, foreground="default")
        theme.name = (
            "arcatom-" + self.palette.label + self.palette.accent.lstrip("#")
        )
        self.register_theme(theme)
        self.theme = theme.name
        self.ansi_color = True
        self.transcript_cache.clear()
        self.transcript_signature = None
        if self.main_screen:
            self.main_screen.set_class(self.compact_layout, "compact")
            self.paint(force=True)

    @property
    def compact_layout(self) -> bool:
        """Use compact spacing on small terminals or by preference.

        小终端或用户偏好使用紧凑布局。
        """
        return self.size.height < 28 or bool(
            self.appearance_preferences.get("compact")
        )

    def action_settings(self) -> None:
        """Open settings with reversible theme previews. 打开支持撤销预览的设置。"""
        if self.screen is not self.main_screen:
            return

        def finished(values: dict | None) -> None:
            """Apply only accepted settings and refresh labels.

            仅应用已确认的设置并刷新标签。
            """
            if values is not None:
                self.view_preferences = values
                self.language = values.get("language", "en")
                self.launch(self.save_view_preferences())
            self.apply_appearance(self.view_preferences)
            self.refresh_labels()

        self.push_screen(Settings(self.view_preferences, self.cwd), finished)

    def refresh_labels(self) -> None:
        """Refresh fixed labels without replacing widgets or losing focus/drafts.

        刷新固定标签，保留控件、焦点与草稿。
        """
        from .i18n import ENGLISH

        reverse = {value: key for key, value in ENGLISH.items()}
        self.connection_text = tr(
            reverse.get(self.connection_text, self.connection_text)
        )
        self.query_one("#new-session", Button).label = tr("＋ 新会话 · Ctrl+N")
        self.query_one("#settings", Button).label = tr("设置 / 调色板 · F2")
        self.query_one("#search", Input).placeholder = tr(
            "⌕  搜索会话名称或工作目录…"
        )
        self.query_one(Composer).placeholder = tr(
            "❯ 想做些什么？输入 /help 查看命令"
        )
        self.hide_commands()
        self.transcript_cache.clear()
        self.transcript_signature = None
        self.activity_signature = None
        self.paint(force=True)

    def action_focus_input(self) -> None:
        """Focus the applicable input in the current screen. 聚焦当前界面的输入控件。"""
        if self.screen is self.main_screen:
            if self.current:
                self.focus_composer(edit=True)
                self.screen.set_focus(self.query_one(Composer))
            else:
                self.screen.set_focus(self.query_one("#search"))
        else:
            inputs = self.screen.query(Input)
            if inputs:
                inputs.first().focus()

    def copy_to_clipboard(self, text: str) -> None:
        """Write an explicit copy request asynchronously. 异步执行用户明确请求的复制。"""
        self._clipboard = text

        async def write() -> None:
            """Perform the requested filesystem or clipboard write.

            执行已请求的文件或剪贴板写入。
            """
            copied = await asyncio.to_thread(copy_text, text)
            if copied:
                self.notify(tr("已复制到系统剪贴板。"))
            else:
                super(AtomXApp, self).copy_to_clipboard(text)
                self.notify(
                    tr(
                        "系统剪贴板不可用，已尝试终端复制；请检查终端剪贴板权限。"
                    ),
                    severity="warning",
                )

        self.launch(write())

    def action_copy_selection(self) -> None:
        """Copy selected text or the latest assistant response. 复制所选文字或最近回复。"""
        focused = self.screen.focused
        text = (
            getattr(focused, "selected_text", "")
            or self.screen.get_selected_text()
        )
        if not text and self.screen is self.main_screen and self.current:
            text = next(
                (
                    i.get("text", "")
                    for i in reversed(
                        list(self.store.get(self.current or "").items.values())
                    )
                    if i.get("type") == "agentMessage"
                ),
                "",
            )
        if text:
            self.copy_to_clipboard(text)
        else:
            self.notify(tr("请先选择文字，或打开一段已有回复的会话。"))

    def action_paste_clipboard(self) -> None:
        """Read the clipboard only for an explicit paste action.

        仅在主动粘贴时读取剪贴板。
        """
        if self.screen is not self.main_screen or not self.current:
            focused = self.screen.focused
            if isinstance(focused, (Input, TextArea)):
                focused.action_paste()
            return
        if self.pasting:
            return
        tid = self.current
        self.pasting = True

        async def paste() -> None:
            """Attach clipboard content to the captured session.

            将剪贴板内容附到确定的会话。
            """
            try:
                content = await asyncio.to_thread(
                    read_clipboard, self.attachment_cache
                )
                if tid in self.store.removed:
                    return
                session = self.store.get(tid)
                session.attachments.extend(content.images)
                if content.text:
                    if self.current == tid:
                        self.focus_composer(edit=True)
                        self.query_one(Composer).insert(content.text)
                    else:
                        session.draft += content.text
                if not content.images and not content.text:
                    self.notify(
                        tr("剪贴板不可用或为空。可用 F4 选择图片文件。"),
                        severity="warning",
                    )
                self.paint(force=True)
            finally:
                self.pasting = False

        self.launch(paste())

    def action_attach_image(self) -> None:
        """Choose an image path for the current session. 为当前会话选择图片路径。"""
        if self.screen is not self.main_screen or not self.current:
            return
        tid = self.current

        async def attach() -> None:
            """Import the selected image unless its thread was removed.

            会话仍存在时导入图片。
            """
            path = await self.push_screen_wait(Prompt(tr("图片文件路径")))
            if path:
                saved = await asyncio.to_thread(
                    import_image, path, self.attachment_cache
                )
                if tid not in self.store.removed:
                    self.store.get(tid).attachments.append(saved)
                    self.paint(force=True)

        self.launch(attach())

    def action_attachments(self) -> None:
        """Open removal choices for pending images. 打开待发送图片的移除选项。"""
        if self.screen is not self.main_screen or not self.current:
            return
        tid = self.current

        async def manage() -> None:
            """Remove only the explicitly chosen attachment. 仅移除用户选定的附件。"""
            session = self.store.get(tid)
            if not session.attachments:
                self.notify(
                    tr("当前没有待发送的图片。Ctrl+V 粘贴，F4 选择文件。")
                )
                return
            chosen = await self.choose(
                tr("选择要移除的图片 · Esc 返回"),
                [
                    (p, f"{i + 1}. {Path(p).name}")
                    for i, p in enumerate(session.attachments)
                ],
            )
            if chosen in session.attachments:
                session.attachments.remove(chosen)
                self.paint(force=True)

        self.launch(manage())

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        yield Static(brand(self.palette, self.compact_layout, True), id="brand")
        yield Static("", id="connection", markup=False)
        with ContentSwitcher(initial="home", id="view"):
            with Vertical(id="home"):
                yield Static("", id="overview", markup=False)
                with Horizontal(id="home-actions"):
                    yield HomeButton(tr("＋ 新会话 · Ctrl+N"), id="new-session")
                    yield HomeButton(tr("设置 / 调色板 · F2"), id="settings")
                yield Static("", id="session-columns")
                yield SessionList(id="sessions")
                yield OptionList(id="home-commands", classes="command-menu")
                yield SessionSearch(
                    placeholder=tr("⌕  搜索会话名称或工作目录…"), id="search"
                )
                yield Static(
                    tr(
                        "↑↓ 选择会话 · Enter 打开 · 打字搜索 · Ctrl+N 新建 · F2 设置"
                    ),
                    id="home-hint",
                    classes="muted",
                )
            with Vertical(id="chat"):
                yield Static("", id="chat-title", markup=False)
                yield Static("", id="chat-path", markup=False, classes="muted")
                yield Button(tr("加载更早记录 · 顶部按 ↑"), id="older-history")
                with TranscriptScroll(id="transcript-scroll"):
                    yield SelectableTranscript(
                        "", id="transcript", markup=False
                    )
                    yield MediaTranscript(id="media-transcript")
                yield Static("", id="waiting", markup=False)
                yield Static("", id="activity-summary", markup=False)
                yield OptionList(id="activities")
                yield OptionList(id="slash-commands", classes="command-menu")
                yield Static("", id="attachments", markup=False)
                yield Composer(
                    id="composer",
                    show_line_numbers=False,
                    soft_wrap=True,
                    placeholder=tr("❯ 想做些什么？输入 /help 查看命令"),
                )
                yield Static(
                    tr(
                        "↑↓ 历史输入  PgUp 浏览  ← 首页  Ctrl+L 输入  F3 复制  F2 设置"
                    ),
                    id="chat-hint",
                    classes="muted",
                )
        yield Static(tr("正在读取用量…"), id="bottom", markup=False)

    def on_mount(self) -> None:
        """Initialize controls after mounting. 挂载后初始化控件。"""
        self.main_screen = self.screen
        self.main_screen.set_class(self.compact_layout, "compact")
        self.query_one("#activities").display = False
        self.query_one("#waiting").display = False
        self.query_one("#slash-commands").display = False
        self.query_one("#home-commands").display = False
        self.query_one("#attachments").display = False
        self.query_one("#media-transcript").display = False
        self.query_one("#older-history").display = False
        self.query_one("#sessions").focus()
        self.set_interval(0.15, self.paint)
        self.set_interval(0.35, self.advance_directory)
        self.run_worker(self.connect(), name="connect", exit_on_error=False)

    def on_resize(self, event: events.Resize) -> None:
        """Schedule layout-dependent refresh after dimensions settle.

        布局稳定后刷新。
        """
        if self.main_screen:
            self.main_screen.set_class(self.compact_layout, "compact")
            self.query_one("#brand", Static).update(
                brand(self.palette, self.compact_layout, not self.current)
            )
            self.paint_status()
            if not self.current:
                self.call_after_refresh(self.paint_sessions)
            else:
                self.call_after_refresh(self.query_one(Composer).fit_height)

    def launch(self, coro: Coroutine[Any, Any, Any]) -> None:
        """Run a guarded asynchronous UI task. 启动带错误处理的异步界面任务。"""

        async def guarded() -> None:
            """Surface task errors without crashing the UI. 显示任务错误并保持界面可用。"""
            try:
                await coro
            except Exception as exc:
                self.notify(clean(str(exc)), severity="error", timeout=10)

        self.run_worker(guarded(), exit_on_error=False)

    def paint_terminal_title(self) -> None:
        """Update the terminal tab even when history is unchanged. 日志不变也更新标签。"""
        sessions = (
            list(self.store.sessions.values()) if not self.current else []
        )
        self.title = build_title(
            self.store.sessions.get(self.current or ""),
            ready=self.ready,
            sending=self.current in self.sending,
            mode=self.view_preferences.get("title", "session"),
            tick=int(time.monotonic() * 4),
            working=sum(s.section == "working" for s in sessions),
            waiting=sum(s.section == "waiting" for s in sessions),
        )
        write_title(self._driver, self.title)

    def paint(self, force: bool = False) -> None:
        """Refresh changed views and the current status line. 更新变化的界面与状态栏。"""
        if self._exit or not self.query("#waiting"):
            return
        self.paint_terminal_title()
        if self.main_screen:
            self.paint_waiting()
            if int(time.time()) != self.last_status_second:
                self.last_status_second = int(time.time())
                self.paint_status()
        if not self.main_screen or (
            not force and self.last_revision == self.store.revision
        ):
            return
        self.last_revision = self.store.revision
        self.main_screen.set_class(bool(self.current), "chat-view")
        self.query_one("#brand", Static).update(
            brand(self.palette, self.compact_layout, not self.current)
        )
        self.query_one("#connection", Static).update(
            Text(
                self.connection_text,
                style=self.palette.success
                if self.ready
                else self.palette.accent,
            )
        )
        self.paint_status()
        if not self.current:
            self.paint_sessions()
        if self.current:
            self.paint_chat()

    def check_action(
        self, action: str, parameters: tuple[object, ...]
    ) -> bool | None:
        """Keep Ctrl+X as text cut outside the list. 聊天输入保留剪切按键。"""
        if action == "delete_session":
            return bool(
                self.screen is self.main_screen and self.current is None
            )
        return True

    @on(OptionList.OptionHighlighted, "#sessions")
    def session_highlighted(self) -> None:
        """Reset deletion confirmation and directory animation on selection.

        选择变化后重置确认与动画。
        """
        options = self.query_one("#sessions", SessionList)
        selected = (
            options.get_option_at_index(options.highlighted).id
            if options.highlighted is not None and options.option_count
            else None
        )
        if self.delete_confirmation and self.delete_confirmation[0] != selected:
            self.delete_confirmation = None
        if self.marquee_tid != selected:
            previous = self.marquee_tid
            self.marquee_tid, self.marquee_step = selected, 0
            self.redraw_directory(previous, 0)
            self.redraw_directory(selected, 0)
        self.paint_status()

    @on(Button.Pressed, "#older-history")
    def older_history_pressed(self) -> None:
        """Request earlier messages from the history button. 通过按钮请求更早记录。"""
        self.load_older_history()

    @on(Composer.Back)
    def back(self) -> None:
        """Return to the home session list. 返回首页会话列表。"""
        self.show_home()

    @on(Composer.Submitted)
    def submit(self) -> None:
        """Accept the current input if it is valid. 输入有效时确认。"""
        if self.current:
            self.launch(self.send_prompt(self.current))

    def active_command_menu(self) -> OptionList:
        """Return the command menu for the visible workspace. 返回当前界面的命令菜单。"""
        return self.query_one(
            "#slash-commands" if self.current else "#home-commands", OptionList
        )

    def hide_commands(self) -> None:
        """Dismiss suggestions without altering the input. 隐藏建议并保留输入。"""
        self.command_matches = []
        if self.main_screen:
            self.command_dismissed = (
                self.query_one(Composer).text
                if self.current
                else self.query_one("#search", Input).value
            )
            self.query_one("#slash-commands").display = False
            self.query_one("#home-commands").display = False
            self.update_navigation_hint()
            self.query_one("#home-hint", Static).update(
                tr(
                    "↑↓ 选择会话 · Enter 打开 · 打字搜索 · Ctrl+N 新建 · F2 设置"
                )
            )

    def refresh_commands(self, text: str) -> None:
        """Filter slash suggestions using the current input. 根据输入筛选斜杠命令。"""
        if self.screen is not self.main_screen:
            return
        self.command_matches = (
            matches(text) if text != self.command_dismissed else []
        )
        menu = self.active_command_menu()
        menu.clear_options()
        for command in self.command_matches:
            label = Text("/" + command.name, style=self.palette.accent)
            label.append(
                "  " + tr(command.description), style=self.palette.muted
            )
            if command.native:
                label.append(tr("  [原生]"), style="#82becb")
            menu.add_option(Option(label, id=command.name))
        menu.display = bool(self.command_matches)
        if menu.option_count:
            menu.highlighted = 0
            hint = (
                tr("↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 收起   ")
                + str(menu.option_count)
                + tr(" 个命令")
            )
            self.query_one(
                "#chat-hint" if self.current else "#home-hint", Static
            ).update(hint)
        else:
            self.update_navigation_hint()

    @on(TextArea.Changed, "#composer")
    def composer_changed(self) -> None:
        """Resize the composer and update matching commands. 调整输入高度并刷新命令匹配。"""
        composer = self.query_one(Composer)
        self.refresh_commands(composer.text)
        self.call_after_refresh(composer.fit_height)

    @on(Input.Changed, "#search")
    def command_search_changed(self, event: Input.Changed) -> None:
        """Refresh home command suggestions after input changes. 首页输入改变后刷新建议。"""
        if not self.current:
            self.refresh_commands(event.value)

    def command_key(self, key: str, source: Input | TextArea) -> bool:
        """Handle suggestion navigation before ordinary text editing.

        优先处理建议列表导航。
        """
        if not self.command_matches or self.screen is not self.main_screen:
            return False
        menu = self.active_command_menu()
        if key in ("up", "down"):
            (
                menu.action_cursor_up
                if key == "up"
                else menu.action_cursor_down
            )()
            return True
        if key in ("enter", "tab") and menu.highlighted is not None:
            name = menu.get_option_at_index(menu.highlighted).id
            self.accept_command(name or "", complete_only=key == "tab")
            return True
        return False

    def accept_command(self, name: str, complete_only: bool = False) -> None:
        """Complete or execute the selected slash command. 补全或执行所选斜杠命令。"""
        text = "/" + name
        self.hide_commands()
        source = (
            self.query_one(Composer)
            if self.current
            else self.query_one("#search", Input)
        )
        if complete_only:
            text += " " if BY_NAME[name].argument else ""
            self.command_dismissed = text
            if isinstance(source, Composer):
                source.load_text(text)
                source.move_cursor(source.document.end)
            else:
                source.value = text
                source.cursor_position = len(text)
            source.focus()
        elif self.current:
            self.launch(self.slash(text))
        else:
            self.launch(self.home_command(text))

    @on(OptionList.OptionSelected, "#slash-commands")
    @on(OptionList.OptionSelected, "#home-commands")
    def command_clicked(self, event: OptionList.OptionSelected) -> None:
        """Accept a command selected in either suggestion list.

        接受任一建议列表的所选命令。
        """
        self.accept_command(event.option.id or "")

    async def home_command(self, text: str) -> None:
        """Dispatch a home command without creating an unnecessary thread.

        首页命令按需创建会话。
        """
        if self.home_command_starting:
            return
        self.home_command_starting = True
        try:
            name = text.split()[0][1:]
            if name not in BY_NAME:
                self.notify(
                    tr("没有这个命令；输入 / 可搜索全部命令。"),
                    severity="warning",
                )
                return
            self.query_one("#search", Input).value = ""
            if (
                name
                not in (
                    "help",
                    "quit",
                    "exit",
                    "new",
                    "clear",
                    "resume",
                    "agents",
                    "warnings",
                    "theme",
                    "settings",
                    "palette",
                )
                and not self.current
            ):
                if not self.ready:
                    raise RpcError(tr("尚未连接 Codex，请先 Ctrl+R 重连。"))
                await self.create_session(self.cwd)
            await self.slash(text)
        finally:
            self.home_command_starting = False

    def action_new_session(self) -> None:
        """Open the working-directory chooser for a new thread. 打开新会话目录选择。"""
        if self.screen is not self.main_screen or not self.ready:
            return
        cwd = (
            self.store.get(self.current or "").meta.get("cwd", self.cwd)
            if self.current
            else self.cwd
        )
        cwd = self.view_preferences.get("default_cwd") or cwd
        self.push_screen(
            NewSession(cwd),
            lambda result: (
                self.launch(self.create_session(result)) if result else None
            ),
        )

    @on(Button.Pressed, "#new-session")
    def new_session_button(self) -> None:
        """Start session creation from its home button. 通过首页按钮新建会话。"""
        self.action_new_session()

    @on(Button.Pressed, "#settings")
    def settings_button(self) -> None:
        """Open settings from the home button. 通过首页按钮打开设置。"""
        self.action_settings()

    def action_refresh_sessions(self) -> None:
        """Reconnect or refresh stored threads and activity. 重连或刷新会话及活动。"""
        if self.screen is not self.main_screen:
            return
        if not self.ready:

            async def reconnect() -> None:
                """Rebuild subscriptions after a transport failure.

                连接失败后重建订阅。
                """
                await self.client.close()
                self.client = CodexClient(
                    binary=getattr(self.client, "binary", "codex"), cwd=self.cwd
                )
                for session in self.store.sessions.values():
                    session.resumed = False
                    session.active_turn = None
                await self.connect()
                if self.current:
                    await self.open_session(self.current)

            self.launch(reconnect())
        else:
            self.launch(self.load_sessions())
            self.launch(self.load_account())
            if self.current:
                session = self.store.get(self.current or "")
                if session.history_error and not session.history_loading:
                    session.history_loading = True
                    self.launch(self.load_recent_history(session.id))

    def action_activity(self) -> None:
        """Toggle details for agents, commands and background terminals.

        切换代理与进程详情。
        """
        if self.screen is not self.main_screen or not self.current:
            return
        self.detail_open = not self.detail_open
        panel = self.query_one("#activities", OptionList)
        panel.display = self.detail_open
        (panel if self.detail_open else self.query_one("#composer")).focus()
        self.paint(force=True)

    @on(OptionList.OptionSelected, "#activities")
    def show_activity(self, event: OptionList.OptionSelected) -> None:
        """Open details for the selected activity target. 打开所选活动的详情。"""
        target = self.activity_targets.get(event.option.id or "")
        if not target:
            return
        kind, value = target
        tid = self.current

        async def show() -> None:
            """Resolve the selected activity before showing its details.

            解析所选活动后显示详情。
            """
            if kind == "agent":

                async def render_agent() -> Group:
                    """Read a child agent transcript for its live detail view.

                    读取子代理记录用于详情。
                    """
                    child = await self.read_history(value)
                    body = [
                        pretty(i, self.code_theme, self.palette)
                        for i in list(child.items.values())[-400:]
                    ]
                    return Group(*(b for b in body if b is not None))

                body = await render_agent()
                self.push_screen(
                    Detail(
                        tr("子代理 · ") + self.store.get(value).title,
                        body,
                        render_agent,
                    )
                )
            else:

                async def render_command() -> Text:
                    """Read command output for its live detail view.

                    读取命令输出用于详情。
                    """
                    assert tid is not None
                    session = self.store.get(tid)
                    item = session.items.get(
                        value if kind == "command" else value.get("itemId"), {}
                    )
                    text = clean(
                        item.get("command")
                        or (
                            value.get("command")
                            if isinstance(value, dict)
                            else ""
                        )
                    )
                    text += "\n\n" + clean(
                        item.get("aggregatedOutput")
                        or tr(
                            "尚无可用输出。外部启动的后台进程可能没有历史日志。"
                        )
                    )
                    if item.get("exitCode") is not None:
                        text += tr("\n\n退出码：{0}").format(item["exitCode"])
                    return Text(text)

                self.push_screen(
                    Detail(
                        tr("命令输出"), await render_command(), render_command
                    )
                )

        self.launch(show())

    async def refresh_activity(self, tid: str) -> None:
        """Refresh background terminals for the selected thread. 刷新指定会话的后台终端。"""
        session = self.store.get(tid)
        try:
            async for batch in self.client.pages(
                "thread/list",
                {
                    "ancestorThreadId": tid,
                    "sourceKinds": [
                        "subAgent",
                        "subAgentThreadSpawn",
                        "subAgentOther",
                    ],
                    "modelProviders": [],
                    "limit": 100,
                },
            ):
                for meta in batch:
                    child = self.store.merge(meta)
                    latest_usage = await asyncio.to_thread(
                        rollout_usage, meta.get("path")
                    )
                    if latest_usage:
                        child.usage = latest_usage
        except RpcError:
            pass  # Collab events still provide agent discovery on older runtimes.
        try:
            data = []
            async for batch in self.client.pages(
                "thread/backgroundTerminals/list",
                {"threadId": tid, "limit": 100},
            ):
                data.extend(batch)
            session.terminals = data
            session.terminal_error = None
        except RpcError as exc:
            session.terminal_error = str(exc)
        self.store.revision += 1

    def poll_current(self) -> None:
        """Schedule activity refresh for the visible session. 定期刷新可见会话的活动。"""
        if self.ready and self.current and not self.native_active:
            self.run_worker(
                self.refresh_activity(self.current),
                group="activity",
                exclusive=True,
                exit_on_error=False,
            )

    def action_usage(self) -> None:
        """Show account limits and session usage details. 展示账户限额与会话用量。"""
        if self.screen is not self.main_screen:
            return
        lines = [tr("账户用量（服务端统计）"), ""]
        summary = self.store.account_usage.get("summary", {})
        lines.append(tr("累计 Token：") + number(summary.get("lifetimeTokens")))
        for day in (self.store.account_usage.get("dailyUsageBuckets") or [])[
            -14:
        ]:
            lines.append(
                f"  {day.get('startDate', '')}   {number(day.get('tokens'))}"
            )
        limits = self.store.rate_limits.get("rateLimitsByLimitId") or {
            "Codex": self.store.rate_limits.get("rateLimits", {})
        }
        for name, limit in limits.items():
            for window in ("primary", "secondary"):
                usage = limit.get(window)
                if usage:
                    percent = usage.get("usedPercent")
                    minutes = usage.get("windowDurationMins")
                    reset = usage.get("resetsAt")
                    lines.append(
                        tr("{0} · {1} 分钟窗口：已用 {2}%").format(
                            name,
                            minutes if minutes is not None else "—",
                            percent if percent is not None else "—",
                        )
                    )
                    if reset:
                        lines.append(
                            tr("  重置时间：")
                            + time.strftime(
                                "%m-%d %H:%M", time.localtime(reset)
                            )
                        )
        lines.extend(
            [
                "",
                tr(
                    "本地会话计数（缓存属于输入，不重复相加；继承历史可能重叠，不等于账单）"
                ),
                "",
            ]
        )
        for session in self.store.roots():
            total = session.usage.get("total", {})
            lines.append(
                tr("{0}\n  总计 {1} · 输入 {2} · 输出 {3} · 缓存 {4}").format(
                    session.title[:45],
                    number(session.total),
                    number(total.get("inputTokens")),
                    number(total.get("outputTokens")),
                    number(total.get("cachedInputTokens")),
                )
            )
        lines.extend(["", tr("— 表示尚无数据。历史列表默认不含归档会话。")])
        if self.store.account_errors:
            lines.append(tr("账户额度暂不可用；本地会话计数仍可查看。"))
        self.push_screen(Detail(tr("用量概览"), Text(clean("\n".join(lines)))))

    def action_escape(self) -> None:
        """Dismiss overlays, stop work, browse or return home in order.

        按层级处理取消、停止与返回。
        """
        if self.screen is not self.main_screen:
            # Modals own Escape before task interruption. 弹窗优先处理 Esc。
            if isinstance(self.screen, Settings):
                self.screen.action_cancel()
            elif isinstance(self.screen, Approval):
                self.screen.dismiss(False)
            else:
                self.screen.dismiss(None)
        elif self.current and (
            self.store.get(self.current or "").active_turn
            or self.current in self.sending
            or self.current in self.stop_requested
            or self.store.get(self.current or "")
            .meta.get("status", {})
            .get("type")
            == "active"
        ):
            self.action_interrupt()
        elif self.command_matches:
            self.hide_commands()
        elif self.current:
            if (
                self.query_one(Composer).has_focus
                and not self.query_one(Composer).read_only
            ):
                self.leave_composer()
            elif self.detail_open and self.query_one("#activities").has_focus:
                self.action_activity()
            else:
                self.show_home()
        elif self.query_one("#search").has_focus:
            self.move_home_focus(1)

    def action_interrupt(self) -> None:
        """Stop the current turn without changing focus. 停止任务并保留当前焦点。"""
        if self.screen is not self.main_screen or not self.current:
            return
        tid = self.current
        self.stop_requested.add(tid)
        self.launch(self.interrupt_turn(tid))

    def action_request_quit(self) -> None:
        """Save UI preferences before leaving this client. 退出当前客户端前保存界面偏好。"""
        if self.screen is not self.main_screen:
            return
        active = any(s.active_turn for s in self.store.sessions.values())
        if active and not getattr(self.client, "shared", False):
            self.push_screen(
                Approval(
                    tr("仍有任务运行中"),
                    tr(
                        "退出会关闭本应用启动的 Codex 服务，正在运行的任务可能中断。是否退出？"
                    ),
                ),
                lambda yes: self.exit() if yes else None,
            )
        else:
            self.exit()

    async def on_unmount(self) -> None:
        """Release the client connection after the UI closes. 界面关闭后释放客户端连接。"""
        await self.client.close()

    @on(Input.Changed, "#search")
    def search(self) -> None:
        """Refresh rows matching the current search text. 刷新符合搜索条件的条目。"""
        self.paint_sessions()

    @on(Input.Submitted, "#search")
    def enter_search(self) -> None:
        """Open the selected match or start a thread from empty input.

        打开匹配会话或空输入新建。
        """
        value = self.query_one("#search", Input).value
        if value.startswith("/"):
            self.launch(self.home_command(value))
            return
        if not value.strip():
            if self.ready:
                self.launch(
                    self.create_session(
                        self.view_preferences.get("default_cwd") or self.cwd
                    )
                )
            return
        options = self.query_one("#sessions", SessionList)
        index = next(iter(options.selectable_indices()), None)
        if index is not None:
            self.launch(
                self.open_session(options.get_option_at_index(index).id or "")
            )

    @on(OptionList.OptionSelected, "#sessions")
    def choose_session(self, event: OptionList.OptionSelected) -> None:
        """Open only a selectable session row. 仅打开可选会话条目。"""
        self.launch(self.open_session(event.option.id or ""))


# Preserve third-party imports while the public product is renamed. 兼容旧导入。
ArcatomApp = AtomXApp
