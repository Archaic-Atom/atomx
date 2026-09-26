"""Approval, question and detail screens. 审批、问题与详情弹窗。"""

from __future__ import annotations

import asyncio
import webbrowser
from collections.abc import Awaitable, Callable
from pathlib import Path

from rich.console import RenderableType
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, OptionList, Static
from textual.widgets.option_list import Option

from .backend.demo import DemoClient
from .backend.rpc import CodexClient, RpcError
from .core.state import clean
from .i18n import tr
from .widgets import SelectableTranscript


class Detail(ModalScreen):
    """Scrollable details with an optional live refresh. 支持实时刷新的可滚动详情。"""

    BINDINGS = [("escape,left", "close", tr("返回"))]

    def __init__(
        self,
        title: str,
        renderable: RenderableType,
        refresh: Callable[[], Awaitable[RenderableType]] | None = None,
    ) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__()
        self.heading, self.renderable = title, renderable
        self.refresh_content = refresh

    def on_mount(self) -> None:
        """Initialize controls after mounting. 挂载后初始化控件。"""
        self.call_after_refresh(self.scroll_to_latest)
        if self.refresh_content:
            self.set_interval(2, self.refresh_detail)

    def scroll_to_latest(self) -> None:
        """Start at the newest output after layout. 排版完成后定位到最新输出。"""
        self.query_one("#detail-scroll", VerticalScroll).scroll_end(
            animate=False, immediate=True
        )

    async def refresh_detail(self) -> None:
        """Refresh live details while preserving scroll position.

        更新实时详情并保留滚动位置。
        """
        if self.app.screen is not self or self.refresh_content is None:
            return
        try:
            body = await self.refresh_content()
            scroll = self.query_one("#detail-scroll", VerticalScroll)
            follow = scroll.is_vertical_scroll_end
            self.query_one("#detail-body", SelectableTranscript).update(body)
            if follow:
                scroll.scroll_end(animate=False, immediate=True)
        except RpcError:
            pass  # Keep the last readable output after the runtime disconnects.

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="detail-scroll"):
                yield SelectableTranscript(
                    self.renderable, id="detail-body", markup=False
                )
            yield Button(tr("返回 · Esc"), id="close")

    def action_close(self) -> None:
        """Close the details screen. 关闭详情页。"""
        self.dismiss()

    @on(Button.Pressed, "#close")
    def close_button(self) -> None:
        """Close details from its button. 通过按钮关闭详情。"""
        self.dismiss()


class ActivityDetail(Detail):
    """Keep thread text selectable and command output one level deeper.

    保持线程文字可复制，命令输出在下一层按回车展开。
    """

    def __init__(
        self,
        title: str,
        renderable: RenderableType,
        entries: list[tuple[str, str]],
        refresh: Callable[
            [], Awaitable[tuple[RenderableType, list[tuple[str, str]]]]
        ],
        open_entry: Callable[[str], Awaitable[None]],
    ) -> None:
        """Store live content and command navigation. 保存实时正文及命令导航。"""
        super().__init__(title, renderable)
        self.entries = entries
        self.refresh_activity_content = refresh
        self.open_entry = open_entry

    def compose(self) -> ComposeResult:
        """Show transcript above collapsed command choices. 正文下方显示折叠命令。"""
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="detail-scroll"):
                yield SelectableTranscript(
                    self.renderable, id="detail-body", markup=False
                )
            yield Static(
                tr("命令与工具 · Enter 展开"), id="activity-entry-title"
            )
            yield OptionList(
                *(Option(label, id=key) for key, label in self.entries),
                id="activity-entries",
            )
            yield Button(tr("返回 · Esc"), id="close")

    def on_mount(self) -> None:
        """Focus commands and refresh while the thread is visible. 聚焦命令并实时更新。"""
        entries = self.query_one("#activity-entries", OptionList)
        entries.display = bool(self.entries)
        if self.entries:
            entries.highlighted = len(self.entries) - 1
            entries.focus()
        self.call_after_refresh(self.scroll_to_latest)
        self.set_interval(2, self.refresh_detail)

    async def refresh_detail(self) -> None:
        """Refresh transcript and command list, preserving the selected command.

        更新正文和命令列表，保留当前选择。
        """
        if self.app.screen is not self:
            return
        try:
            body, entries = await self.refresh_activity_content()
            scroll = self.query_one("#detail-scroll", VerticalScroll)
            follow = scroll.is_vertical_scroll_end
            self.query_one("#detail-body", SelectableTranscript).update(body)
            if follow:
                scroll.scroll_end(animate=False, immediate=True)
            if entries != self.entries:
                options = self.query_one("#activity-entries", OptionList)
                current = options.highlighted
                at_latest = current is None or current >= len(self.entries) - 1
                options.clear_options()
                options.add_options(
                    Option(label, id=key) for key, label in entries
                )
                options.display = bool(entries)
                if entries:
                    options.highlighted = (
                        len(entries) - 1
                        if at_latest
                        else min(current or 0, len(entries) - 1)
                    )
                self.entries = entries
        except RpcError:
            pass

    @on(OptionList.OptionSelected, "#activity-entries")
    async def selected_entry(self, event: OptionList.OptionSelected) -> None:
        """Expand one command after Enter. 回车展开单条命令。"""
        if event.option.id:
            await self.open_entry(event.option.id)


class Login(ModalScreen[bool]):
    """Complete Codex-managed authentication in a browser.

    通过 Codex 管理的浏览器流程登录，不接触账户密码或令牌。
    """

    BINDINGS = [("escape", "cancel", tr("稍后"))]

    def __init__(self, client: CodexClient | DemoClient) -> None:
        """Keep only the backend client and the current login ID. 保存当前登录会话。"""
        super().__init__()
        self.client = client
        self.login_id: str | None = None
        self.auth_url: str | None = None
        self.user_code: str | None = None
        self.starting = False

    def compose(self) -> ComposeResult:
        """Offer official browser and device-code flows. 提供官方网页和设备码登录。"""
        with Vertical(id="small-dialog"):
            yield Static(tr("登录 Codex"), id="dialog-title")
            yield Static(
                tr(
                    "AtomX 使用本机 Codex 登录。请选择一种方式，在浏览器完成验证。"
                ),
                classes="muted",
            )
            yield Static("", id="login-status")
            yield Static("", id="login-link", markup=False)
            yield Static("", id="login-code", markup=False)
            with Horizontal(id="login-link-actions"):
                yield Button(tr("使用 ChatGPT 登录"), id="login-chatgpt")
                yield Button(tr("设备码登录"), id="login-device")
            with Horizontal(id="dialog-actions"):
                yield Button(tr("打开浏览器"), id="login-open", disabled=True)
                yield Button(tr("复制链接"), id="login-copy", disabled=True)
                yield Button(
                    tr("复制验证码"), id="login-copy-code", disabled=True
                )
            yield Button(tr("稍后 · Esc"), id="login-later")

    def on_mount(self) -> None:
        """Focus the normal browser login. 默认选中浏览器登录。"""
        self.query_one("#login-chatgpt", Button).focus()

    async def start_login(self, kind: str) -> None:
        """Request an official login URL and open it after a user click.

        用户选择后请求官方链接并打开浏览器。
        """
        if self.starting:
            return
        self.starting = True
        self.query_one("#login-status", Static).update(tr("正在准备登录…"))
        old_login_id = self.login_id
        self.login_id = None
        self.auth_url = None
        self.user_code = None
        self.query_one("#login-link", Static).update("")
        self.query_one("#login-code", Static).update("")
        for button_id in ("login-open", "login-copy", "login-copy-code"):
            self.query_one("#" + button_id, Button).disabled = True
        try:
            if old_login_id:
                try:
                    await self.client.call(
                        "account/login/cancel",
                        {"loginId": old_login_id},
                        timeout=5,
                    )
                except RpcError:
                    pass
            response = await self.client.call(
                "account/login/start", {"type": kind}, timeout=30
            )
            self.login_id = response.get("loginId")
            self.auth_url = response.get("authUrl") or response.get(
                "verificationUrl"
            )
            self.user_code = response.get("userCode")
            if not self.login_id or not self.auth_url:
                raise RpcError(tr("Codex 没有返回登录链接。"))
            if not self.auth_url.startswith("https://"):
                raise RpcError(
                    tr("登录链接不是 HTTPS，请改用官方 codex login 命令。")
                )
            self.query_one("#login-link", Static).update(self.auth_url)
            self.query_one("#login-code", Static).update(
                tr("验证码：{0}").format(self.user_code)
                if self.user_code
                else ""
            )
            self.query_one("#login-open", Button).disabled = False
            self.query_one("#login-copy", Button).disabled = False
            self.query_one("#login-copy-code", Button).disabled = not bool(
                self.user_code
            )
            self.query_one("#login-status", Static).update(
                tr("在浏览器完成验证后，AtomX 会自动继续。")
            )
            await self.open_browser()
        except (RpcError, OSError) as exc:
            self.query_one("#login-status", Static).update(clean(str(exc)))
        finally:
            self.starting = False

    async def open_browser(self) -> None:
        """Open the backend-provided HTTPS authorization URL. 打开官方 HTTPS 授权链接。"""
        if not self.auth_url:
            return
        if not self.auth_url.startswith("https://"):
            self.query_one("#login-status", Static).update(
                tr("登录链接不是 HTTPS，请改用官方 codex login 命令。")
            )
            return
        if not await asyncio.to_thread(webbrowser.open, self.auth_url):
            self.query_one("#login-status", Static).update(
                tr("浏览器未能自动打开，请复制链接手动打开。")
            )

    def complete(self, params: dict) -> None:
        """Accept only completion of this login attempt. 只处理当前登录请求的结果。"""
        if not self.login_id or params.get("loginId") != self.login_id:
            return
        if params.get("success"):
            self.login_id = None
            self.dismiss(True)
        else:
            self.login_id = None
            self.query_one("#login-status", Static).update(
                clean(params.get("error") or tr("登录未完成，请重试。"))
            )

    @on(Button.Pressed)
    async def pressed(self, event: Button.Pressed) -> None:
        """Route explicit login, copy, and cancel choices. 处理登录、复制和取消。"""
        choice = event.button.id
        if choice in ("login-chatgpt", "login-device"):
            await self.start_login(
                "chatgpt" if choice == "login-chatgpt" else "chatgptDeviceCode"
            )
        elif choice == "login-open":
            await self.open_browser()
        elif choice == "login-copy" and self.auth_url:
            self.app.copy_to_clipboard(self.auth_url)
        elif choice == "login-copy-code" and self.user_code:
            self.app.copy_to_clipboard(self.user_code)
        elif choice == "login-later":
            await self.action_cancel()

    async def action_cancel(self) -> None:
        """Stop a pending attempt before leaving. 离开前取消未完成的登录请求。"""
        if self.login_id:
            login_id = self.login_id
            self.login_id = None
            try:
                await self.client.call(
                    "account/login/cancel", {"loginId": login_id}, timeout=5
                )
            except RpcError:
                pass
        self.dismiss(False)


class NewSession(ModalScreen[str | None]):
    """Validate a working directory before creating a session. 新建会话前验证目录。"""

    BINDINGS = [("escape", "cancel", tr("取消"))]

    def __init__(self, cwd: str) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__()
        self.cwd = cwd

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="small-dialog"):
            yield Static(tr("✦ 新会话"), id="dialog-title")
            yield Static(tr("工作目录"), classes="muted")
            yield Input(self.cwd, id="directory")
            yield Static(tr("Enter 创建 · Esc 返回"), classes="muted")

    def on_mount(self) -> None:
        """Initialize controls after mounting. 挂载后初始化控件。"""
        self.query_one(Input).focus()

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        """Accept the current input if it is valid. 输入有效时确认。"""
        path = Path(event.value).expanduser().resolve()
        if path.is_dir():
            self.dismiss(str(path))
        else:
            self.notify(
                tr("目录不存在，请输入一个已有目录"), severity="warning"
            )

    def action_cancel(self) -> None:
        """Return to the previous interaction level. 返回上一交互层级。"""
        self.dismiss(None)


class Approval(ModalScreen[bool]):
    """Return an explicit one-time approval or denial. 返回明确的一次性批准或拒绝。"""

    BINDINGS = [("escape", "decline", tr("拒绝"))]

    def __init__(
        self, title: str, details: str, can_accept: bool = True
    ) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__()
        self.heading, self.details, self.can_accept = title, details, can_accept

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="detail-scroll"):
                yield Static(Text(clean(self.details)), markup=False)
            with Horizontal(id="dialog-actions"):
                yield Button(tr("拒绝 · Esc"), id="deny")
                if self.can_accept:
                    yield Button(
                        tr("仅允许这一次"), id="allow", variant="warning"
                    )

    def on_mount(self) -> None:
        """Initialize controls after mounting. 挂载后初始化控件。"""
        self.query_one("#deny", Button).focus()

    @on(Button.Pressed)
    def select(self, event: Button.Pressed) -> None:
        """Return the explicitly selected approval decision. 返回用户明确选择的审批结果。"""
        self.dismiss(event.button.id == "allow")

    def action_decline(self) -> None:
        """Decline the pending approval request. 拒绝当前审批请求。"""
        self.dismiss(False)


class Question(ModalScreen[str | None]):
    """Offer suggested answers and optional free-form input. 提供选项与自由输入回答。"""

    BINDINGS = [("escape", "cancel", tr("取消"))]

    def __init__(self, question: dict) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="dialog"):
            yield Static(
                Text(
                    clean(
                        self.question.get("question")
                        or self.question.get("title")
                    )
                ),
                id="dialog-title",
            )
            choices = []
            for option in self.question.get("options") or []:
                label = (
                    option.get("label", "")
                    if isinstance(option, dict)
                    else option
                )
                description = (
                    option.get("description", "")
                    if isinstance(option, dict)
                    else ""
                )
                choices.append(
                    Option(Text(clean(label + "  " + description)), id=label)
                )
            yield OptionList(*choices, id="answers")
            yield Input(
                placeholder=tr("也可以输入你的回答，再按 Enter"),
                id="custom-answer",
                password=bool(self.question.get("isSecret")),
            )
            yield Button(tr("取消"), id="cancel")

    @on(OptionList.OptionSelected)
    def choose(self, event: OptionList.OptionSelected) -> None:
        """Return the selected option identifier. 返回所选选项的标识。"""
        self.dismiss(event.option.id)

    @on(Input.Submitted)
    def custom(self, event: Input.Submitted) -> None:
        """Return a nonempty free-form answer. 返回非空的自定义回答。"""
        if event.value.strip():
            self.dismiss(event.value)

    @on(Button.Pressed)
    def cancel_button(self) -> None:
        """Dismiss without accepting changes. 取消且不接受修改。"""
        self.dismiss(None)

    def action_cancel(self) -> None:
        """Return to the previous interaction level. 返回上一交互层级。"""
        self.dismiss(None)
