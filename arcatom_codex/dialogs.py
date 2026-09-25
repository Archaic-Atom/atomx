"""Approval, question and detail screens. 审批、问题与详情弹窗。"""

from __future__ import annotations

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

from .i18n import tr
from .rpc import RpcError
from .state import clean


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
        if self.refresh_content:
            self.set_interval(2, self.refresh_detail)

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
            self.query_one("#detail-body", Static).update(body)
            if follow:
                scroll.scroll_end(animate=False, immediate=True)
        except RpcError:
            pass  # Keep the last readable output after the runtime disconnects.

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            with VerticalScroll(id="detail-scroll"):
                yield Static(self.renderable, id="detail-body", markup=False)
            yield Button(tr("返回 · Esc"), id="close")

    def action_close(self) -> None:
        """Close the details screen. 关闭详情页。"""
        self.dismiss()

    @on(Button.Pressed, "#close")
    def close_button(self) -> None:
        """Close details from its button. 通过按钮关闭详情。"""
        self.dismiss()


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
