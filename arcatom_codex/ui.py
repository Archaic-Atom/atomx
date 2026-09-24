from __future__ import annotations

from .i18n import tr
import asyncio
import json
from pathlib import Path
import time
import uuid

from rich.console import Group
from rich.markdown import Markdown as RichMarkdown
from rich.padding import Padding
from rich.text import Text
from textual import events, on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, ContentSwitcher, Input, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from .rpc import CodexClient, RpcError
from .state import Store, Session, clean, number, rollout_usage
from .statusline import build_status
from .personal import bridge_instructions, discover_skills, turn_context
from .commands import matches, BY_NAME
from .command_actions import CommandActions
from .preferences import read_preferences
from .appearance import PALETTES, palette_for, brand
from .settings import Settings
from .clipboard import copy_text, read_clipboard, import_image
from .pickers import Prompt
from .home_list import section_heading, session_row

def pretty(item: dict, code_theme="monokai", palette=None):
    palette = palette or PALETTES["warm"]
    kind = item.get("type", "")
    if kind == "userMessage":
        text = "\n".join(c.get("text", tr('[图片或附件]')) for c in item.get("content", []))
        return Group(Padding(Text(tr('❯ 你\n') + clean(text), style="bold " + palette.foreground),
                             (0, 1), style="on " + palette.user), Text(""))
    if kind in ("agentMessage", "plan"):
        return Group(Text("✦ Codex" if kind == "agentMessage" else tr('◇ 计划'), style=palette.accent),
                     RichMarkdown(clean(item.get("text")), code_theme=code_theme), Text(""))
    if kind == "commandExecution":
        status = item.get("status", "inProgress")
        mark = "●" if status == "inProgress" else "✓" if status == "completed" else "!"
        return Text(f"  {mark} {clean(item.get('command', tr('执行命令')))}\n", style=palette.muted)
    if kind == "fileChange":
        paths = ", ".join(clean(c.get("path")) for c in item.get("changes", []))
        return Text(tr('  ◇ 文件修改 · {0}\n').format(paths), style=palette.muted)
    if kind == "mcpToolCall":
        return Text(f"  ◇ {clean(item.get('server'))} / {clean(item.get('tool'))} · {clean(item.get('status'))}\n", style=palette.muted)
    if kind == "webSearch":
        return Text(tr('  ⌕ 搜索 · {0}\n').format(clean(item.get('query'))), style=palette.muted)
    if kind == "notice":
        return Text("! " + clean(item.get("text")) + "\n", style=palette.warning)
    return None


class Composer(TextArea):
    BINDINGS = [Binding("enter", "submit", show=False),
                Binding("shift+enter,ctrl+j", "newline", show=False)]

    class Submitted(Message):
        pass

    class Back(Message):
        pass

    def reset_history(self):
        self.history_index = None
        self.history_draft = ""

    def recall(self, direction: int) -> bool:
        """Recall per-session prompts while preserving unsent edits. 历史输入不丢草稿。"""
        if not self.app.current:
            return False
        entries = ["\n".join(c.get("text", "") for c in i.get("content", [])
                              if c.get("type") == "text")
                   for i in self.app.store.get(self.app.current).items.values()
                   if i.get("type") == "userMessage"]
        entries = [text for text in entries if text]
        index = getattr(self, "history_index", None)
        if index is None:
            if direction > 0 or not entries:
                return False
            self.history_draft = self.text
            index = len(entries)
        index = max(0, min(len(entries), index + direction))
        self.history_index = None if index == len(entries) else index
        self.load_text(self.history_draft if index == len(entries) else entries[index])
        self.move_cursor(self.document.end)
        return True

    async def _on_key(self, event: events.Key):
        if self.read_only:
            if event.key == "enter":
                self.app.focus_composer(edit=True)
            elif event.key == "left":
                self.app.show_home()
            elif event.key in ("up", "down", "pageup", "pagedown"):
                self.app.browse_transcript(event.key)
            else:
                await super()._on_key(event)
                return
            event.stop()
            event.prevent_default()
            return
        if self.app.command_key(event.key, self):
            event.stop()
            event.prevent_default()
            return
        if event.key in ("up", "down") and (
                getattr(self, "history_index", None) is not None or
                (event.key == "up" and self.cursor_location[0] == 0)):
            if self.recall(-1 if event.key == "up" else 1):
                event.stop()
                event.prevent_default()
                return
        if event.key in ("pageup", "pagedown"):
            event.stop()
            event.prevent_default()
            self.app.browse_transcript(event.key)
            return
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.action_submit()
        else:
            await super()._on_key(event)

    def action_submit(self):
        if self.read_only:
            self.app.focus_composer(edit=True)
        else:
            self.post_message(self.Submitted())

    def on_click(self):
        self.app.focus_composer(edit=True)

    def action_newline(self):
        self.insert("\n")

    def action_cursor_left(self):
        if self.text == "":
            self.post_message(self.Back())
        else:
            super().action_cursor_left()


class SessionSearch(Input):
    async def _on_key(self, event: events.Key):
        if self.app.command_key(event.key, self):
            event.stop()
            event.prevent_default()
            return
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            options = self.app.query_one("#sessions", OptionList)
            self.app.home_selection_active = True
            if event.key == "up":
                options.action_cursor_up()
            else:
                options.action_cursor_down()
        else:
            await super()._on_key(event)


class TranscriptScroll(VerticalScroll):
    """Arrow navigation returns naturally to the composer. 方向键浏览后返回输入。"""

    async def on_key(self, event: events.Key):
        if event.key == "left":
            event.stop()
            event.prevent_default()
            self.app.show_home()
        elif event.key == "enter":
            event.stop()
            event.prevent_default()
            self.app.focus_composer(edit=True)
        elif event.key in ("up", "down", "pageup", "pagedown", "home", "end"):
            event.stop()
            event.prevent_default()
            self.app.browse_transcript(event.key)


class Detail(ModalScreen):
    BINDINGS = [("escape,left", "close", tr('返回'))]

    def __init__(self, title: str, renderable, refresh=None):
        super().__init__()
        self.heading, self.renderable = title, renderable
        self.refresh_content = refresh

    def on_mount(self):
        if self.refresh_content:
            self.set_interval(2, self.refresh_detail)

    async def refresh_detail(self):
        if self.app.screen is not self:
            return
        try:
            body = await self.refresh_content()
            scroll = self.query_one("#detail-scroll", VerticalScroll)
            follow = scroll.is_vertical_scroll_end
            self.query_one("#detail-body", Static).update(body)
            if follow:
                scroll.scroll_end(animate=False)
        except RpcError:
            pass  # Keep the last readable output after the runtime disconnects.

    def compose(self):
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="detail-scroll"):
                yield Static(self.renderable, id="detail-body", markup=False)
            yield Button(tr('返回 · Esc'), id="close")

    def action_close(self):
        self.dismiss()

    @on(Button.Pressed, "#close")
    def close_button(self):
        self.dismiss()


class NewSession(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", tr('取消'))]

    def __init__(self, cwd):
        super().__init__()
        self.cwd = cwd

    def compose(self):
        with Vertical(id="small-dialog"):
            yield Static(tr('✦ 新会话'), id="dialog-title")
            yield Static(tr('工作目录'), classes="muted")
            yield Input(self.cwd, id="directory")
            yield Static(tr('Enter 创建 · Esc 返回'), classes="muted")

    def on_mount(self):
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self, event):
        path = Path(event.value).expanduser().resolve()
        if path.is_dir():
            self.dismiss(str(path))
        else:
            self.notify(tr('目录不存在，请输入一个已有目录'), severity="warning")

    def action_cancel(self):
        self.dismiss(None)


class Approval(ModalScreen[bool]):
    BINDINGS = [("escape", "decline", tr('拒绝'))]

    def __init__(self, title, details, can_accept=True):
        super().__init__()
        self.heading, self.details, self.can_accept = title, details, can_accept

    def compose(self):
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="detail-scroll"):
                yield Static(Text(clean(self.details)), markup=False)
            with Horizontal(id="dialog-actions"):
                yield Button(tr('拒绝 · Esc'), id="deny")
                if self.can_accept:
                    yield Button(tr('仅允许这一次'), id="allow", variant="warning")

    def on_mount(self):
        self.query_one("#deny", Button).focus()

    @on(Button.Pressed)
    def select(self, event):
        self.dismiss(event.button.id == "allow")

    def action_decline(self):
        self.dismiss(False)


class Question(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", tr('取消'))]

    def __init__(self, question):
        super().__init__()
        self.question = question

    def compose(self):
        with Vertical(id="dialog"):
            yield Static(Text(clean(self.question.get("question") or self.question.get("title"))), id="dialog-title")
            choices = []
            for option in self.question.get("options") or []:
                label = option.get("label", "") if isinstance(option, dict) else option
                description = option.get("description", "") if isinstance(option, dict) else ""
                choices.append(Option(Text(clean(label + "  " + description)), id=label))
            yield OptionList(*choices, id="answers")
            yield Input(placeholder=tr('也可以输入你的回答，再按 Enter'), id="custom-answer",
                        password=bool(self.question.get("isSecret")))
            yield Button(tr('取消'), id="cancel")

    @on(OptionList.OptionSelected)
    def choose(self, event):
        self.dismiss(event.option.id)

    @on(Input.Submitted)
    def custom(self, event):
        if event.value.strip():
            self.dismiss(event.value)

    @on(Button.Pressed)
    def cancel_button(self):
        self.dismiss(None)

    def action_cancel(self):
        self.dismiss(None)


class ArcatomApp(CommandActions, App):
    TITLE = "Arcatom Codex"
    CSS_PATH = "style.tcss"
    BINDINGS = [
        Binding("ctrl+n", "new_session", tr('新会话'), priority=True),
        Binding("f2", "settings", tr('设置'), priority=True),
        Binding("ctrl+l,f6", "focus_input", tr('回到输入框'), priority=True),
        Binding("f3,ctrl+shift+c", "copy_selection", tr('复制'), priority=True),
        Binding("ctrl+v", "paste_clipboard", tr('粘贴文字 / 图片'), priority=True),
        Binding("f4", "attach_image", tr('添加图片'), priority=True),
        Binding("f8", "attachments", tr('管理图片'), priority=True),
        Binding("ctrl+u", "usage", tr('用量'), priority=True),
        Binding("ctrl+t", "activity", tr('代理与进程'), priority=True),
        Binding("ctrl+r", "refresh_sessions", tr('刷新'), priority=True),
        Binding("ctrl+q", "request_quit", tr('退出'), priority=True),
        Binding("ctrl+c", "interrupt", tr('停止任务'), priority=True),
        Binding("ctrl+x", "delete_session", tr('删除会话'), priority=True),
        Binding("escape", "escape", tr('返回'), priority=True),
    ]

    def __init__(self, cwd: str, client=None, demo=False):
        super().__init__()
        self.cwd, self.demo = cwd, demo
        self.client = client or CodexClient(cwd=cwd)
        self.store = Store()
        self.current: str | None = None
        self.ready = False
        self.detail_open = False
        self.sending: set[str] = set()
        self.last_revision = -1
        self.requests = asyncio.Queue()
        self.activity_targets = {}
        self.main_screen = None
        self.connection_text = tr('正在连接本机 Codex…')
        self.poll_timer = None
        self.account_timer = None
        self.account_loading = False
        self.deleting: set[str] = set()
        self.last_status_second = -1
        self.transcript_cache: dict[str, tuple] = {}
        self.transcript_signature = None
        self.activity_signature = None
        self.personal_skills = [] if demo else discover_skills()
        self.personal_instructions = "" if demo else bridge_instructions(self.personal_skills)
        self.resume_locks = {}
        self.command_busy = False
        self.home_command_starting = False
        self.command_matches = []
        self.command_dismissed = None
        self.raw_transcript = False
        self.view_preferences = {} if demo else read_preferences()
        self.language = "zh" if self.view_preferences.get("language") == "zh" else "en"
        self.status_fields = ["time", "tokens", "context", "limits", "model"]
        fields = self.view_preferences.get("status_fields")
        if isinstance(fields, list) and fields and all(f in self.status_fields for f in fields):
            self.status_fields = list(dict.fromkeys(fields))
        from pygments.styles import get_all_styles
        theme = self.view_preferences.get("code_theme", "monokai")
        self.code_theme = theme if isinstance(theme, str) and theme in set(get_all_styles()) else "monokai"
        self.native_active = False
        self.home_selection_active = False
        self.pasting = False
        self.creating = False
        self.last_navigation_key = None
        self.last_navigation_time = 0.0
        self.attachment_cache = Path.home() / ".cache" / "arcatom" / "attachments"
        self.appearance_preferences = dict(self.view_preferences)
        self.palette = palette_for(self.view_preferences)
        self.apply_appearance(self.view_preferences)

    def apply_appearance(self, preferences: dict):
        """Update widgets and cached Rich messages together. 同步 CSS 和历史消息。"""
        self.appearance_preferences = dict(preferences)
        self.palette = palette_for(preferences)
        theme = self.palette.theme()
        theme.name = "arcatom-" + self.palette.label + self.palette.accent.lstrip("#")
        self.register_theme(theme)
        self.theme = theme.name
        self.transcript_cache.clear()
        self.transcript_signature = None
        if self.main_screen:
            self.main_screen.set_class(self.compact_layout, "compact")
            self.paint(force=True)

    @property
    def compact_layout(self) -> bool:
        return self.size.height < 28 or bool(self.appearance_preferences.get("compact"))

    def action_settings(self):
        if self.screen is not self.main_screen:
            return
        def finished(values):
            if values is not None:
                self.view_preferences = values
                self.language = values.get("language", "en")
                self.launch(self.save_view_preferences())
            self.apply_appearance(self.view_preferences)
            self.refresh_labels()
        self.push_screen(Settings(self.view_preferences, self.cwd), finished)

    def refresh_labels(self):
        """Refresh fixed labels without replacing widgets or losing focus/drafts."""
        from .i18n import ENGLISH
        reverse = {value: key for key, value in ENGLISH.items()}
        self.connection_text = tr(reverse.get(self.connection_text, self.connection_text))
        self.query_one("#new-session", Button).label = tr("＋ 新会话 · Ctrl+N")
        self.query_one("#settings", Button).label = tr("设置 / 调色板 · F2")
        self.query_one("#search", Input).placeholder = tr("⌕  搜索会话名称或工作目录…")
        self.query_one(Composer).placeholder = tr("❯ 想做些什么？输入 /help 查看命令")
        self.hide_commands()
        self.transcript_cache.clear()
        self.transcript_signature = None
        self.activity_signature = None
        self.paint(force=True)

    def action_focus_input(self):
        if self.screen is self.main_screen:
            if self.current:
                self.focus_composer(edit=True)
            else:
                self.query_one("#search").focus()
        else:
            inputs = self.screen.query(Input)
            if inputs:
                inputs.first().focus()

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        if not self.demo:
            self.launch(asyncio.to_thread(copy_text, text))

    def action_copy_selection(self):
        focused = self.screen.focused
        text = getattr(focused, "selected_text", "") or self.screen.get_selected_text()
        if not text and self.screen is self.main_screen and self.current:
            text = next((i.get("text", "") for i in reversed(list(
                self.store.get(self.current).items.values())) if i.get("type") == "agentMessage"), "")
        if text:
            self.copy_to_clipboard(text)
            self.notify(tr('已复制。没有选择文字时，F3 复制最近回复。'))
        else:
            self.notify(tr('请先选择文字，或打开一段已有回复的会话。'))

    def action_paste_clipboard(self):
        if self.screen is not self.main_screen or not self.current:
            focused = self.screen.focused
            if isinstance(focused, (Input, TextArea)):
                focused.action_paste()
            return
        if self.pasting:
            return
        tid = self.current
        self.pasting = True
        async def paste():
            try:
                content = await asyncio.to_thread(read_clipboard, self.attachment_cache)
                if tid in self.store.removed:
                    return
                session = self.store.get(tid)
                session.attachments.extend(content.images)
                if content.text:
                    if self.current == tid:
                        self.query_one(Composer).insert(content.text)
                    else:
                        session.draft += content.text
                if not content.images and not content.text:
                    self.notify(tr('剪贴板不可用或为空。可用 F4 选择图片文件。'), severity="warning")
                self.paint(force=True)
            finally:
                self.pasting = False
        self.launch(paste())

    def action_attach_image(self):
        if self.screen is not self.main_screen or not self.current:
            return
        tid = self.current
        async def attach():
            path = await self.push_screen_wait(Prompt(tr('图片文件路径')))
            if path:
                saved = await asyncio.to_thread(import_image, path, self.attachment_cache)
                if tid not in self.store.removed:
                    self.store.get(tid).attachments.append(saved)
                    self.paint(force=True)
        self.launch(attach())

    def action_attachments(self):
        if self.screen is not self.main_screen or not self.current:
            return
        tid = self.current
        async def manage():
            session = self.store.get(tid)
            if not session.attachments:
                self.notify(tr('当前没有待发送的图片。Ctrl+V 粘贴，F4 选择文件。'))
                return
            chosen = await self.choose(tr('选择要移除的图片 · Esc 返回'), [
                (p, f"{i + 1}. {Path(p).name}") for i, p in enumerate(session.attachments)])
            if chosen in session.attachments:
                session.attachments.remove(chosen)
                self.paint(force=True)
        self.launch(manage())

    def update_navigation_hint(self):
        """Show the active keyboard mode. 明确编辑、浏览或输入框选择状态。"""
        if self.command_matches:
            return
        composer = self.query_one(Composer)
        if not composer.read_only:
            hint = tr("编辑 · Esc 浏览 · ↑↓ 历史输入 · Ctrl+J 换行 · F3 复制 · F2 设置")
        elif composer.has_focus:
            hint = tr("输入框已选中 · Enter 编辑 · ↑ 浏览 · Esc / ← 首页")
        else:
            hint = tr("浏览 · ↑↓ 滚动 · ↑↑ 顶部 · ↓↓ 底部 · ↓ 回输入框 · Esc / ← 首页")
        self.query_one("#chat-hint", Static).update(hint)

    def focus_composer(self, edit: bool):
        """Selecting the input never sends a draft. 选中输入框不会误发草稿。"""
        composer = self.query_one(Composer)
        composer.read_only = not edit
        composer.focus()
        self.last_navigation_key = None
        self.update_navigation_hint()

    def leave_composer(self):
        self.hide_commands()
        self.query_one(Composer).read_only = True
        self.query_one("#transcript-scroll").focus()
        self.last_navigation_key = None
        self.update_navigation_hint()

    def browse_transcript(self, key: str):
        """Browse with arrows; a quick repeated arrow jumps to either end."""
        scroll = self.query_one("#transcript-scroll", TranscriptScroll)
        if key in ("down", "pagedown") and scroll.is_vertical_scroll_end:
            self.focus_composer(edit=False)
            return
        self.query_one(Composer).read_only = True
        scroll.focus()
        now = time.monotonic()
        repeated = (key in ("up", "down") and key == self.last_navigation_key
                    and now - self.last_navigation_time <= 0.35)
        self.last_navigation_key = None if repeated else key
        self.last_navigation_time = now
        if repeated or key in ("home", "end"):
            if key in ("up", "home"):
                scroll.scroll_home(animate=False)
            else:
                scroll.scroll_end(animate=False)
            self.update_navigation_hint()
            return
        step = max(1, scroll.size.height - 2) if key in ("pageup", "pagedown") else 3
        scroll.scroll_relative(y=-step if key in ("up", "pageup") else step, animate=False)
        self.update_navigation_hint()

    def compose(self) -> ComposeResult:
        yield Static(brand(self.palette, self.compact_layout, True), id="brand")
        yield Static("", id="connection", markup=False)
        with ContentSwitcher(initial="home", id="view"):
            with Vertical(id="home"):
                yield Static("", id="overview", markup=False)
                with Horizontal(id="home-actions"):
                    yield Button(tr('＋ 新会话 · Ctrl+N'), id="new-session")
                    yield Button(tr('设置 / 调色板 · F2'), id="settings")
                yield SessionSearch(placeholder=tr('⌕  搜索会话名称或工作目录…'), id="search")
                yield OptionList(id="home-commands", classes="command-menu")
                yield OptionList(id="sessions")
                yield Static(tr('Enter 新建 · ↑↓ 历史 · Ctrl+X 删除 · Ctrl+N 目录 · F2 设置'), id="home-hint", classes="muted")
            with Vertical(id="chat"):
                yield Static("", id="chat-title", markup=False)
                yield Static("", id="chat-path", markup=False, classes="muted")
                with TranscriptScroll(id="transcript-scroll"):
                    yield Static("", id="transcript", markup=False)
                yield Static("", id="waiting", markup=False)
                yield Static("", id="activity-summary", markup=False)
                yield OptionList(id="activities")
                yield OptionList(id="slash-commands", classes="command-menu")
                yield Static("", id="attachments", markup=False)
                yield Composer(id="composer", show_line_numbers=False, soft_wrap=True,
                               placeholder=tr('❯ 想做些什么？输入 /help 查看命令'))
                yield Static(tr('↑↓ 历史输入  PgUp 浏览  ← 首页  Ctrl+L 输入  F3 复制  F2 设置'), id="chat-hint", classes="muted")
        yield Static(tr('正在读取用量…'), id="bottom", markup=False)

    def on_mount(self):
        self.main_screen = self.screen
        self.main_screen.set_class(self.compact_layout, "compact")
        self.query_one("#activities").display = False
        self.query_one("#waiting").display = False
        self.query_one("#slash-commands").display = False
        self.query_one("#home-commands").display = False
        self.query_one("#attachments").display = False
        self.query_one("#search").focus()
        self.set_interval(0.15, self.paint)
        self.run_worker(self.connect(), name="connect", exit_on_error=False)

    def on_resize(self, event: events.Resize):
        if self.main_screen:
            self.main_screen.set_class(self.compact_layout, "compact")
            self.query_one("#brand", Static).update(brand(self.palette, self.compact_layout, not self.current))
            self.paint_status()
            if not self.current:
                self.call_after_refresh(self.paint_sessions)

    async def connect(self):
        try:
            await self.client.start()
            self.ready = True
            self.connection_text = tr('● 演示模式 · 以下为示例数据，不会调用模型') if self.demo else tr('● 已连接本机 Codex')
            self.run_worker(self.consume(), group="rpc-events", exclusive=True, exit_on_error=False)
            self.run_worker(self.approvals(), group="rpc-approvals", exclusive=True, exit_on_error=False)
            await self.load_sessions()
            self.run_worker(self.load_account(), exit_on_error=False)
            if self.poll_timer is None:
                self.poll_timer = self.set_interval(4, self.poll_current)
            if self.account_timer is None:
                self.account_timer = self.set_interval(60, self.refresh_account)
        except Exception as exc:
            self.connection_text = tr('! 连接失败 · Ctrl+R 重试')
            self.notify(clean(str(exc)), severity="error", timeout=15)
        self.paint(force=True)

    async def load_sessions(self):
        async for batch in self.client.pages("thread/list", {"limit": 100, "modelProviders": [], "sortKey": "updated_at"}):
            for meta in batch:
                meta["archived"] = False
                self.store.merge(meta)
            self.paint(force=True)
            # Usage snapshots are read on a worker thread; no account credentials are read.
            usage = await asyncio.to_thread(lambda: {t["id"]: rollout_usage(t.get("path")) for t in batch})
            for tid, value in usage.items():
                if value and not self.store.get(tid).usage:
                    self.store.get(tid).usage = value
        self.store.revision += 1

    async def load_account(self):
        if self.account_loading:
            return
        self.account_loading = True
        self.store.account_errors = []
        async def read(method, field):
            try:
                setattr(self.store, field, await self.client.call(method, timeout=12))
            except RpcError as exc:
                self.store.account_errors.append(f"{method}: {exc}")
        try:
            await asyncio.gather(read("account/usage/read", "account_usage"), read("account/rateLimits/read", "rate_limits"))
        finally:
            self.account_loading = False
            self.store.revision += 1

    def refresh_account(self):
        if self.ready:
            self.launch(self.load_account())

    def launch(self, coro):
        async def guarded():
            try:
                await coro
            except Exception as exc:
                self.notify(clean(str(exc)), severity="error", timeout=10)
        self.run_worker(guarded(), exit_on_error=False)

    async def consume(self):
        while True:
            message = await self.client.events.get()
            if "id" in message:
                tid = message.get("params", {}).get("threadId")
                if tid:
                    self.store.get(tid).pending_requests.add(str(message["id"]))
                    self.store.revision += 1
                await self.requests.put(message)
                continue
            method, params = message.get("method", ""), message.get("params", {})
            if method == "client/disconnected":
                self.ready = False
                for session in self.store.sessions.values():
                    session.busy_since = None
                    session.active_turn = None
                    session.pending_requests.clear()
                    session.meta["status"] = {"type": "notLoaded"}
                self.connection_text = tr('! Codex 已断开 · Ctrl+R 重连')
                self.notify(clean(params.get("message")), severity="error")
            else:
                self.store.event(method, params)
                if self.current in self.store.removed:
                    self.current = None
                    self.show_home()
                if params.get("threadId") == self.current and self.current:
                    self.store.get(self.current).unread = False
            if method == "turn/completed" and params.get("threadId") != self.current:
                session = self.store.get(params["threadId"])
                self.notify(session.title[:50] + tr(' · 任务已结束'))

    async def approvals(self):
        while True:
            message = await self.requests.get()
            method, params, rid = message["method"], message.get("params", {}), message["id"]
            tid = params.get("threadId")
            title = self.store.get(tid).title if tid else "Codex"
            try:
                if method in ("item/commandExecution/requestApproval", "item/fileChange/requestApproval"):
                    details = tr('会话：{0}\n\n').format(title) + json.dumps(params, ensure_ascii=False, indent=2)
                    if tid and params.get("itemId"):
                        item = self.store.get(tid).items.get(params["itemId"], {})
                        details += "\n\n" + json.dumps(item, ensure_ascii=False, indent=2)
                    decisions = params.get("availableDecisions")
                    accepted = await self.push_screen_wait(Approval(tr('需要你的确认'), details, not decisions or "accept" in decisions))
                    await self.client.reply(rid, {"decision": "accept" if accepted else "decline"})
                elif method == "item/permissions/requestApproval":
                    accepted = await self.push_screen_wait(Approval(tr('请求额外权限 · ') + title, json.dumps(params, ensure_ascii=False, indent=2)))
                    await self.client.reply(rid, {"permissions": params.get("permissions", {}) if accepted else {}, "scope": "turn"})
                elif method == "item/tool/requestUserInput":
                    answers = {}
                    for question in params.get("questions", []):
                        answer = await self.push_screen_wait(Question(question))
                        answers[question["id"]] = {"answers": [] if answer is None else [answer]}
                    await self.client.reply(rid, {"answers": answers})
                elif method == "mcpServer/elicitation/request":
                    await self.push_screen_wait(Approval(tr('此扩展表单暂不支持 · ') + title,
                                                      json.dumps(params, ensure_ascii=False, indent=2), False))
                    await self.client.reply(rid, {"action": "decline", "content": None})
                else:
                    await self.client.reject_unsupported(rid)
                    self.notify(tr('暂不支持的交互已拒绝：') + method, severity="warning")
            except RpcError as exc:
                self.notify(str(exc), severity="error")
            finally:
                if tid:
                    self.store.get(tid).pending_requests.discard(str(rid))
                    self.store.revision += 1

    def paint(self, force=False):
        if self._exit or not self.query("#waiting"):
            return
        if self.main_screen:
            self.paint_waiting()
            if int(time.time()) != self.last_status_second:
                self.last_status_second = int(time.time())
                self.paint_status()
        if not self.main_screen or (not force and self.last_revision == self.store.revision):
            return
        self.last_revision = self.store.revision
        self.main_screen.set_class(bool(self.current), "chat-view")
        self.query_one("#brand", Static).update(brand(self.palette, self.compact_layout, not self.current))
        self.query_one("#connection", Static).update(Text(self.connection_text, style=self.palette.success if self.ready else self.palette.accent))
        self.paint_status()
        if not self.current:
            self.paint_sessions()
        if self.current:
            self.paint_chat()

    def paint_status(self):
        """Follow the user's Claude status line. 复用用户的状态栏规范。"""
        session = self.store.get(self.current) if self.current else None
        if session is None:
            options = self.query_one("#sessions", OptionList)
            if options.highlighted is not None and options.option_count:
                tid = options.get_option_at_index(options.highlighted).id
                session = self.store.sessions.get(tid)
        bar = build_status(session, self.store.rate_limits, self.size.width - 6,
                           fields=self.status_fields, palette=self.palette)
        if not self.ready:
            bar.append(tr(' · 未连接'), style=self.palette.accent)
        elif self.store.account_errors:
            bar.append(tr(' · 额度更新失败'), style=self.palette.accent)
        self.query_one("#bottom", Static).update(bar)

    def paint_waiting(self):
        """Animate independently of transcript rendering. 独立刷新等待动画。"""
        widget = self.query_one("#waiting", Static)
        session = self.store.sessions.get(self.current)
        busy = bool(session and self.ready and
                    (session.busy_since is not None or session.active_turn))
        widget.display = busy
        if not busy:
            return
        now = time.monotonic()
        since = session.busy_since if session.busy_since is not None else now
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        frame = frames[int(now * 8) % len(frames)]
        elapsed = max(0, int(now - since))
        widget.update(Text.assemble(
            (f"{frame} {session.phase or tr('等待 Codex')}", self.palette.accent),
            (f"  {elapsed}s" + (tr(' · Ctrl+C 停止') if session.active_turn else ""), self.palette.muted),
        ))

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Keep Ctrl+X as text cut outside the list. 聊天输入保留剪切按键。"""
        if action == "delete_session":
            return bool(self.screen is self.main_screen and self.current is None)
        return True

    @on(OptionList.OptionHighlighted, "#sessions")
    def session_highlighted(self):
        self.paint_status()

    def action_delete_session(self):
        """Delete only the selected history entry. 仅删除用户选中的历史会话。"""
        if not self.ready or self.current is not None or self.screen is not self.main_screen:
            return
        options = self.query_one("#sessions", OptionList)
        if options.highlighted is None or not options.option_count:
            return
        tid = options.get_option_at_index(options.highlighted).id
        if tid not in self.store.sessions:
            return
        if tid in self.deleting:
            return
        session = self.store.get(tid)
        if session.active_turn or tid in self.sending or session.pending_requests or session.meta.get("status", {}).get("type") == "active":
            self.notify(tr('这个会话还在运行，请先进入会话停止任务。'), severity="warning")
            return
        index = options.highlighted
        self.deleting.add(tid)
        self.paint_sessions()
        self.launch(self.delete_session(tid, index))

    async def delete_session(self, tid: str, index: int):
        """Commit deletion after backend success. 后端成功后移除本地条目。"""
        title = self.store.get(tid).title
        try:
            await self.client.call("thread/delete", {"threadId": tid})
            self.store.remove_thread(tid)
            self.paint_sessions()
            options = self.query_one("#sessions", OptionList)
            if options.option_count:
                options.highlighted = min(index, options.option_count - 1)
            self.paint_status()
            self.notify(tr('已删除：') + title[:50])
        except RpcError:
            self.notify(tr('删除未确认，条目暂时保留；Ctrl+R 可刷新检查。'), severity="warning")
            raise
        finally:
            self.deleting.discard(tid)
            self.paint_sessions()

    def paint_sessions(self):
        sessions = self.store.roots(self.query_one("#search", Input).value)
        options = self.query_one("#sessions", OptionList)
        selected = None
        if options.highlighted is not None and options.option_count:
            selected = options.get_option_at_index(options.highlighted).id
        options.clear_options()
        width = max(30, options.size.width - 3)
        for section, label, color in (
                ("waiting", tr('等待你确认 / 输入'), self.palette.warning),
                ("working", tr('正在工作'), self.palette.success),
                ("history", tr('历史会话'), self.palette.muted)):
            members = [s for s in sessions if s.section == section]
            options.add_option(Option(section_heading(label, len(members), color,
                                                      self.palette, section == "waiting"),
                                      disabled=True))
            for session in members:
                options.add_option(Option(session_row(session, width, self.palette,
                                                      session.id in self.deleting), id=session.id))
        if selected:
            for i in range(options.option_count):
                if options.get_option_at_index(i).id == selected:
                    options.highlighted = i
                    break
        if options.highlighted is None or options.get_option_at_index(options.highlighted).disabled:
            options.highlighted = next((i for i in range(options.option_count)
                                        if not options.get_option_at_index(i).disabled), None)
        lifetime = self.store.account_usage.get("summary", {}).get("lifetimeTokens")
        active = sum(s.section == "working" for s in self.store.roots())
        self.query_one("#overview", Static).update(Text(tr('{0} 个会话    {1} 个运行中    账户累计 {2} tokens').format(len(self.store.roots()), active, number(lifetime)), style=self.palette.accent))

    def paint_chat(self):
        session = self.store.get(self.current)
        attachments = self.query_one("#attachments", Static)
        attachments.display = bool(session.attachments)
        attachments.update(tr('▧ {0} 张待发送图片 · F8 管理 · Enter 发送').format(len(session.attachments)))
        self.query_one("#chat-title", Static).update(Text("✦  " + session.title[:100], style="bold " + self.palette.accent))
        title_mode = self.view_preferences.get("title", "app")
        self.title = "Arcatom Codex" if title_mode == "app" else ((session.meta.get("model") or "Codex") + " · " if title_mode == "model" else "") + session.title
        model = session.meta.get("model") or tr('默认模型')
        self.query_one("#chat-path", Static).update(clean(f"{session.meta.get('cwd', self.cwd)}  ·  {model}  ·  {session.status}"))
        scroll = self.query_one("#transcript-scroll", VerticalScroll)
        follow = (self.view_preferences.get("follow_output", True)
                  and not scroll.has_focus and scroll.is_vertical_scroll_end)
        # Cache parsed Markdown; don't reparse the whole history for each delta.
        # 缓存未变化的 Markdown，避免每个流式片段重新解析完整历史。
        blocks = []
        signatures = []
        for item in list(session.items.values())[-400:]:
            visible = {k: v for k, v in item.items() if k != "aggregatedOutput"}
            signature = json.dumps(visible, ensure_ascii=False, sort_keys=True)
            cache_key = session.id + ":" + item["id"]
            cached = self.transcript_cache.get(cache_key)
            if cached is None or cached[0] != signature:
                rendered = Text(clean(item.get("text", "")) + "\n") if self.raw_transcript and item.get("type") in ("agentMessage", "plan") else pretty(item, self.code_theme, self.palette)
                cached = (signature, rendered)
                self.transcript_cache[cache_key] = cached
            if cached[1] is not None:
                signatures.append((item["id"], signature))
                blocks.append(cached[1])
        if not blocks:
            blocks = [Text(tr('\n开始一段新的工作。\n\n直接描述任务；需要分工时，可以明确让 Codex 使用子代理。'), style=self.palette.muted)]
        if len(session.items) > 400:
            blocks.insert(0, Text(tr('显示最近 400 项；完整历史保存在 Codex。\n'), style=self.palette.muted))
        transcript_signature = (session.id, signatures)
        if transcript_signature != self.transcript_signature:
            self.transcript_signature = transcript_signature
            self.query_one("#transcript", Static).update(Group(*blocks))
            if follow:
                scroll.scroll_end(animate=False)
        commands = [v for v in session.items.values() if v.get("type") == "commandExecution"]
        self.query_one("#activity-summary", Static).update(Text(
            tr('{0}  子代理 {1}  ·  后台进程 {2}  ·  命令 {3}    Ctrl+T 展开/收起').format('▾' if self.detail_open else '▸', len(session.agents), len(session.terminals), len(commands)), style=self.palette.accent))
        activity_signature = (session.id, json.dumps(session.agents, sort_keys=True),
                              json.dumps(session.terminals, sort_keys=True),
                              [(c["id"], c.get("status"), c.get("exitCode")) for c in commands[-30:]],
                              [(tid, self.store.get(tid).total) for tid in session.agents],
                              session.terminal_error)
        if self.activity_signature == activity_signature:
            return
        self.activity_signature = activity_signature
        options = self.query_one("#activities", OptionList)
        old = options.highlighted
        options.clear_options()
        self.activity_targets = {}
        def add(key, label, target):
            options.add_option(Option(Text(label), id=key))
            self.activity_targets[key] = target
        for tid, agent in session.agents.items():
            status = agent.get("status") or agent.get("runtimeStatus") or "unknown"
            state = {"running": tr('运行中'), "active": tr('运行中'), "completed": tr('已完成'), "idle": tr('就绪'), "shutdown": tr('已关闭'), "errored": tr('异常'), "pendingInit": tr('启动中'), "notLoaded": tr('未加载')}.get(status, status)
            child = self.store.get(tid)
            label = clean(agent.get("name") or child.meta.get("agentNickname") or tid[:8])
            add("a-" + tid, f"  ◇ {label}  ·  {state}  ·  {number(child.total)} tokens", ("agent", tid))
        live_items = set()
        for terminal in session.terminals:
            key = terminal.get("itemId", terminal["processId"])
            live_items.add(key)
            add("p-" + terminal["processId"], f"  ● {clean(terminal['command'])}  ·  PID {terminal.get('osPid') or terminal['processId']}", ("process", terminal))
        for item in commands[-30:]:
            if item["id"] not in live_items:
                status = item.get("status", "unknown")
                suffix = tr('退出码 {0}').format(item['exitCode']) if item.get("exitCode") is not None else status
                add("c-" + item["id"], f"  {'●' if status == 'inProgress' else '✓' if status == 'completed' else '!'} {clean(item.get('command'))}  ·  {suffix}", ("command", item["id"]))
        if not options.option_count:
            options.add_option(Option(Text(tr('当前没有子代理或命令'), style=self.palette.muted), disabled=True))
        if session.terminal_error:
            options.add_option(Option(Text(tr('后台进程信息不可用：') + session.terminal_error[:90], style=self.palette.muted), disabled=True))
        if old is not None and old < options.option_count:
            options.highlighted = old
        elif self.activity_targets:
            options.highlighted = 0

    @on(Input.Changed, "#search")
    def search(self):
        self.home_selection_active = False
        self.paint_sessions()

    @on(Input.Submitted, "#search")
    def enter_search(self):
        value = self.query_one("#search", Input).value
        if value.startswith("/"):
            self.launch(self.home_command(value))
            return
        if not value.strip() and not self.home_selection_active:
            if self.ready:
                self.launch(self.create_session(self.view_preferences.get("default_cwd") or self.cwd))
            return
        options = self.query_one("#sessions", OptionList)
        if options.highlighted is not None:
            tid = options.get_option_at_index(options.highlighted).id
            if tid in self.store.sessions:
                self.launch(self.open_session(tid))

    @on(OptionList.OptionSelected, "#sessions")
    def choose_session(self, event):
        self.launch(self.open_session(event.option.id))

    async def read_history(self, tid):
        result = await self.client.call("thread/read", {"threadId": tid, "includeTurns": True})
        meta = result["thread"]
        if meta.get("historyMode") == "paginated":
            turns = []
            async for page in self.client.pages("thread/turns/list", {"threadId": tid, "limit": 100, "itemsView": "full", "sortDirection": "asc"}):
                turns.extend(page)
            meta["turns"] = turns
        return self.store.merge(meta, history=True)

    async def open_session(self, tid):
        if self.current:
            self.store.get(self.current).draft = self.query_one("#composer", Composer).text
        self.transcript_cache.clear()
        self.transcript_signature = None
        session = self.store.get(tid)
        if not session.hydrated:
            await self.read_history(tid)
        if tid in self.deleting or tid in self.store.removed:
            return
        self.current = tid
        session.unread = False
        self.query_one("#view", ContentSwitcher).current = "chat"
        self.query_one("#composer", Composer).load_text(session.draft)
        self.query_one(Composer).reset_history()
        self.focus_composer(edit=True)
        self.paint(force=True)
        self.query_one("#transcript-scroll", VerticalScroll).scroll_end(animate=False)
        self.launch(self.refresh_activity(tid))

    @on(Composer.Back)
    def back(self):
        self.show_home()

    def show_home(self):
        if self.current:
            self.store.get(self.current).draft = self.query_one("#composer", Composer).text
        self.query_one("#view", ContentSwitcher).current = "home"
        self.current = None
        self.home_selection_active = False
        self.hide_commands()
        self.query_one("#search").focus()
        self.paint(force=True)

    @on(Composer.Submitted)
    def submit(self):
        if self.current:
            self.launch(self.send_prompt(self.current))

    def active_command_menu(self):
        return self.query_one("#slash-commands" if self.current else "#home-commands", OptionList)

    def hide_commands(self):
        self.command_matches = []
        if self.main_screen:
            self.command_dismissed = self.query_one(Composer).text if self.current else self.query_one("#search", Input).value
            self.query_one("#slash-commands").display = False
            self.query_one("#home-commands").display = False
            self.update_navigation_hint()
            self.query_one("#home-hint", Static).update(tr('Enter 新建 · ↑↓ 历史 · / 命令 · Ctrl+X 删除 · Ctrl+N 目录'))

    def refresh_commands(self, text):
        if self.screen is not self.main_screen:
            return
        self.command_matches = matches(text) if text != self.command_dismissed else []
        menu = self.active_command_menu()
        menu.clear_options()
        for command in self.command_matches:
            label = Text("/" + command.name, style=self.palette.accent)
            label.append("  " + tr(command.description), style=self.palette.muted)
            if command.native:
                label.append(tr('  [原生]'), style="#82becb")
            menu.add_option(Option(label, id=command.name))
        menu.display = bool(self.command_matches)
        if menu.option_count:
            menu.highlighted = 0
            hint = tr('↑↓ 选择 · Tab 补全 · Enter 执行 · Esc 收起   ') + str(menu.option_count) + tr(' 个命令')
            self.query_one("#chat-hint" if self.current else "#home-hint", Static).update(hint)
        else:
            self.update_navigation_hint()

    @on(TextArea.Changed, "#composer")
    def composer_changed(self):
        self.refresh_commands(self.query_one(Composer).text)

    @on(Input.Changed, "#search")
    def command_search_changed(self, event):
        if not self.current:
            self.refresh_commands(event.value)

    def command_key(self, key, source):
        if not self.command_matches or self.screen is not self.main_screen:
            return False
        menu = self.active_command_menu()
        if key in ("up", "down"):
            (menu.action_cursor_up if key == "up" else menu.action_cursor_down)()
            return True
        if key in ("enter", "tab") and menu.highlighted is not None:
            name = menu.get_option_at_index(menu.highlighted).id
            self.accept_command(name, complete_only=key == "tab")
            return True
        return False

    def accept_command(self, name, complete_only=False):
        text = "/" + name
        self.hide_commands()
        source = self.query_one(Composer) if self.current else self.query_one("#search", Input)
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
    def command_clicked(self, event):
        self.accept_command(event.option.id)

    async def home_command(self, text):
        if self.home_command_starting:
            return
        self.home_command_starting = True
        try:
            name = text.split()[0][1:]
            if name not in BY_NAME:
                self.notify(tr('没有这个命令；输入 / 可搜索全部命令。'), severity="warning")
                return
            self.query_one("#search", Input).value = ""
            if name not in ("help", "quit", "exit", "new", "clear", "resume", "agents", "warnings", "theme", "settings", "palette") and not self.current:
                if not self.ready:
                    raise RpcError(tr('尚未连接 Codex，请先 Ctrl+R 重连。'))
                await self.create_session(self.cwd)
            await self.slash(text)
        finally:
            self.home_command_starting = False

    async def send_prompt(self, tid: str, prompt_override: str | None = None):
        """Echo before awaiting RPC and retain failed drafts. 先回显，再等待后端。"""
        composer = self.query_one("#composer", Composer)
        prompt = composer.text.strip() if prompt_override is None else prompt_override
        images = list(self.store.get(tid).attachments) if prompt_override is None else []
        if self.pasting and prompt_override is None:
            self.notify(tr("图片正在粘贴，请稍后按 Enter。"))
            return
        if not prompt and not images:
            return
        if tid in self.sending:
            self.notify(tr('上一条消息仍在发送，当前输入已保留。'))
            return
        if prompt_override is None and prompt.startswith("/"):
            await self.slash(prompt)
            return
        if not self.ready:
            self.notify(tr('尚未连接 Codex，请按 Ctrl+R 重试'), severity="warning")
            return
        session = self.store.get(tid)
        content = ([{"type": "text", "text": prompt}] if prompt else []) + [
            {"type": "localImage", "path": path} for path in images]
        message_id = str(uuid.uuid4())
        session.pending_messages[message_id] = prompt
        session.items[message_id] = {
            "id": message_id, "type": "userMessage", "clientId": message_id,
            "content": content,
        }
        session.draft = ""
        if prompt_override is None:
            composer.clear()
            composer.reset_history()
            session.attachments.clear()
        self.sending.add(tid)
        session.busy_since = session.busy_since or time.monotonic()
        session.phase = tr('正在恢复会话') if not session.resumed else tr('正在发送消息')
        self.store.revision += 1
        self.paint(force=True)
        self.query_one("#transcript-scroll", VerticalScroll).scroll_end(animate=False)
        try:
            await self.ensure_resumed(tid)
            session.phase = tr('等待 Codex')
            params = {
                "threadId": tid, "clientUserMessageId": message_id,
                "input": content,
            }
            context = await asyncio.to_thread(
                turn_context, prompt, self.personal_skills, self.personal_instructions
            )
            if context:
                params["additionalContext"] = context
            if session.active_turn:
                params["expectedTurnId"] = session.active_turn
                await self.client.call("turn/steer", params)
            else:
                await self.client.call("turn/start", params)
        except Exception as exc:
            session.attachments[0:0] = images
            # A timeout may have been accepted: do not silently send it again.
            # 超时可能已经被后端接受，显示状态不明，绝不自动重发。
            if message_id in session.pending_messages:
                session.pending_messages.pop(message_id, None)
                session.items.pop(message_id, None)
                session.ingest({
                    "id": "send-error-" + message_id, "type": "notice",
                    "text": tr('消息发送未确认，请先检查会话后再决定是否重试。\n')
                            + prompt + "\n" + str(exc),
                })
                if self.current == tid and not composer.text:
                    composer.load_text(prompt)
                elif self.current != tid and not session.draft:
                    session.draft = prompt
            if not session.active_turn:
                session.busy_since = None
                session.phase = ""
            raise
        finally:
            self.sending.discard(tid)
            self.store.revision += 1

    def action_new_session(self):
        if self.screen is not self.main_screen or not self.ready:
            return
        cwd = self.store.get(self.current).meta.get("cwd", self.cwd) if self.current else self.cwd
        cwd = self.view_preferences.get("default_cwd") or cwd
        self.push_screen(NewSession(cwd), lambda result: self.launch(self.create_session(result)) if result else None)

    @on(Button.Pressed, "#new-session")
    def new_session_button(self):
        self.action_new_session()

    @on(Button.Pressed, "#settings")
    def settings_button(self):
        self.action_settings()

    async def create_session(self, cwd):
        if self.creating:
            return
        self.creating = True
        try:
            result = await self.client.call("thread/start", {"cwd": cwd})
            session = self.store.merge(result["thread"], history=True)
            session.resumed = True
            session.awaiting_input = True
            self.apply_runtime(session, result)
            await self.open_session(session.id)
        finally:
            self.creating = False

    def action_refresh_sessions(self):
        if self.screen is not self.main_screen:
            return
        if not self.ready:
            async def reconnect():
                await self.client.close()
                self.client = CodexClient(cwd=self.cwd)
                for session in self.store.sessions.values():
                    session.resumed = False
                    session.active_turn = None
                await self.connect()
            self.launch(reconnect())
        else:
            self.launch(self.load_sessions())
            self.launch(self.load_account())

    def action_activity(self):
        if self.screen is not self.main_screen or not self.current:
            return
        self.detail_open = not self.detail_open
        panel = self.query_one("#activities", OptionList)
        panel.display = self.detail_open
        (panel if self.detail_open else self.query_one("#composer")).focus()
        self.paint(force=True)

    @on(OptionList.OptionSelected, "#activities")
    def show_activity(self, event):
        target = self.activity_targets.get(event.option.id)
        if not target:
            return
        kind, value = target
        tid = self.current
        async def show():
            if kind == "agent":
                async def render_agent():
                    child = await self.read_history(value)
                    body = [pretty(i, self.code_theme, self.palette) for i in list(child.items.values())[-400:]]
                    return Group(*(b for b in body if b is not None))
                body = await render_agent()
                self.push_screen(Detail(tr('子代理 · ') + self.store.get(value).title, body, render_agent))
            else:
                async def render_command():
                    session = self.store.get(tid)
                    item = session.items.get(value if kind == "command" else value.get("itemId"), {})
                    text = clean(item.get("command") or (value.get("command") if isinstance(value, dict) else ""))
                    text += "\n\n" + clean(item.get("aggregatedOutput") or tr('尚无可用输出。外部启动的后台进程可能没有历史日志。'))
                    if item.get("exitCode") is not None:
                        text += tr('\n\n退出码：{0}').format(item['exitCode'])
                    return Text(text)
                self.push_screen(Detail(tr('命令输出'), await render_command(), render_command))
        self.launch(show())

    async def refresh_activity(self, tid):
        session = self.store.get(tid)
        try:
            async for batch in self.client.pages("thread/list", {"ancestorThreadId": tid, "sourceKinds": ["subAgent", "subAgentThreadSpawn", "subAgentOther"], "modelProviders": [], "limit": 100}):
                for meta in batch:
                    child = self.store.merge(meta)
                    latest_usage = await asyncio.to_thread(rollout_usage, meta.get("path"))
                    if latest_usage:
                        child.usage = latest_usage
        except RpcError:
            pass  # Collab events still provide agent discovery on older runtimes.
        try:
            data = []
            async for batch in self.client.pages("thread/backgroundTerminals/list", {"threadId": tid, "limit": 100}):
                data.extend(batch)
            session.terminals = data
            session.terminal_error = None
        except RpcError as exc:
            session.terminal_error = str(exc)
        self.store.revision += 1

    def poll_current(self):
        if self.ready and self.current and not self.native_active:
            self.run_worker(self.refresh_activity(self.current), group="activity", exclusive=True, exit_on_error=False)

    def action_usage(self):
        if self.screen is not self.main_screen:
            return
        lines = [tr('账户用量（服务端统计）'), ""]
        summary = self.store.account_usage.get("summary", {})
        lines.append(tr('累计 Token：') + number(summary.get("lifetimeTokens")))
        for day in (self.store.account_usage.get("dailyUsageBuckets") or [])[-14:]:
            lines.append(f"  {day.get('startDate', '')}   {number(day.get('tokens'))}")
        limits = self.store.rate_limits.get("rateLimitsByLimitId") or {"Codex": self.store.rate_limits.get("rateLimits", {})}
        for name, limit in limits.items():
            for window in ("primary", "secondary"):
                usage = limit.get(window)
                if usage:
                    percent = usage.get("usedPercent")
                    minutes = usage.get("windowDurationMins")
                    reset = usage.get("resetsAt")
                    lines.append(tr('{0} · {1} 分钟窗口：已用 {2}%').format(name, minutes if minutes is not None else '—', percent if percent is not None else '—'))
                    if reset:
                        lines.append(tr('  重置时间：') + time.strftime("%m-%d %H:%M", time.localtime(reset)))
        lines.extend(["", tr('本地会话计数（缓存属于输入，不重复相加；继承历史可能重叠，不等于账单）'), ""])
        for session in self.store.roots():
            total = session.usage.get("total", {})
            lines.append(tr('{0}\n  总计 {1} · 输入 {2} · 输出 {3} · 缓存 {4}').format(session.title[:45], number(session.total), number(total.get('inputTokens')), number(total.get('outputTokens')), number(total.get('cachedInputTokens'))))
        lines.extend(["", tr('— 表示尚无数据。历史列表默认不含归档会话。')])
        if self.store.account_errors:
            lines.append(tr('账户额度暂不可用；本地会话计数仍可查看。'))
        self.push_screen(Detail(tr('用量概览'), Text(clean("\n".join(lines)))))

    def action_escape(self):
        if self.screen is self.main_screen and self.command_matches:
            self.hide_commands()
        elif self.screen is not self.main_screen:
            # Let the modal handle Esc; never close an approval as accepted.
            if isinstance(self.screen, Approval):
                self.screen.dismiss(False)
            else:
                self.screen.dismiss(None)
        elif self.current:
            if self.query_one(Composer).has_focus and not self.query_one(Composer).read_only:
                self.leave_composer()
            elif self.detail_open and self.query_one("#activities").has_focus:
                self.action_activity()
            else:
                self.show_home()

    def action_interrupt(self):
        focused = self.screen.focused
        if getattr(focused, "selected_text", "") or self.screen.get_selected_text():
            self.action_copy_selection()
            return
        if self.screen is not self.main_screen:
            return
        if self.current and self.store.get(self.current).active_turn:
            session = self.store.get(self.current)
            self.launch(self.client.call("turn/interrupt", {"threadId": session.id, "turnId": session.active_turn}))
        else:
            self.notify(tr('当前没有运行中的回合。Ctrl+Q 退出。'))

    def action_request_quit(self):
        if self.screen is not self.main_screen:
            return
        active = any(s.active_turn for s in self.store.sessions.values())
        if active:
            self.push_screen(Approval(tr('仍有任务运行中'), tr('退出会关闭本应用启动的 Codex 服务，正在运行的任务可能中断。是否退出？')),
                             lambda yes: self.exit() if yes else None)
        else:
            self.exit()

    async def on_unmount(self):
        await self.client.close()
