"""Keyboard-first searchable choices. 可搜索的键盘选择面板。"""

from __future__ import annotations

from rich.text import Text
from textual import events, on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option

from .i18n import tr


class PickerSearch(Input):
    """Send arrows to choices while keeping search editable. 方向键选择且保持搜索可编辑。"""

    async def _on_key(self, event: events.Key) -> None:
        """Route input before the widget applies its default bindings.

        先处理应用按键。
        """
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            options = self.screen.query_one("#choices", OptionList)
            (
                options.action_cursor_up
                if event.key == "up"
                else options.action_cursor_down
            )()
        else:
            await super()._on_key(event)


class Picker(ModalScreen[str | None]):
    """Filter choices and return a selected identifier. 筛选选项并返回选中的标识。"""

    BINDINGS = [("escape", "cancel", tr("取消"))]

    def __init__(
        self,
        title: str,
        choices: list[tuple[str, str]],
        selected: str | None = None,
    ) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__()
        self.heading, self.choices, self.selected = title, choices, selected

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            yield PickerSearch(
                placeholder=tr("输入筛选 · ↑↓ 选择 · Enter 确认 · Esc 取消"),
                id="picker-search",
            )
            yield OptionList(id="choices")

    def on_mount(self) -> None:
        """Initialize controls after mounting. 挂载后初始化控件。"""
        self.populate("")
        self.query_one(Input).focus()

    def populate(self, query: str) -> None:
        """Rebuild filtered options and restore the saved choice.

        重建筛选选项并恢复选择。
        """
        options = self.query_one(OptionList)
        options.clear_options()
        for value, label in self.choices:
            if query.casefold() in (value + " " + label).casefold():
                options.add_option(
                    Option(
                        Text(
                            ("● " if value == self.selected else "  ") + label
                        ),
                        id=value,
                    )
                )
        if options.option_count:
            options.highlighted = 0
            if not query and self.selected:
                for i in range(options.option_count):
                    if options.get_option_at_index(i).id == self.selected:
                        options.highlighted = i
                        break

    @on(Input.Changed)
    def search(self, event: Input.Changed) -> None:
        """Refresh rows matching the current search text. 刷新符合搜索条件的条目。"""
        self.populate(event.value)

    @on(Input.Submitted)
    def submit(self) -> None:
        """Accept the current input if it is valid. 输入有效时确认。"""
        options = self.query_one(OptionList)
        if options.highlighted is not None:
            self.dismiss(options.get_option_at_index(options.highlighted).id)

    @on(OptionList.OptionSelected)
    def choose(self, event: OptionList.OptionSelected) -> None:
        """Return the selected option identifier. 返回所选选项的标识。"""
        self.dismiss(event.option.id)

    def action_cancel(self) -> None:
        """Return to the previous interaction level. 返回上一交互层级。"""
        self.dismiss(None)


class Prompt(ModalScreen[str | None]):
    """Collect one text value with cancel support. 获取可取消的单个文本输入。"""

    BINDINGS = [("escape", "cancel", tr("取消"))]

    def __init__(self, title: str, value: str = "") -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__()
        self.heading, self.value = title, value

    def compose(self) -> ComposeResult:
        """Build the screen or widget tree. 构造界面控件树。"""
        with Vertical(id="small-dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            yield Input(self.value)
            yield Static(tr("Enter 确认 · Esc 取消"))

    @on(Input.Submitted)
    def submit(self, event: Input.Submitted) -> None:
        """Accept the current input if it is valid. 输入有效时确认。"""
        if event.value.strip():
            self.dismiss(event.value.strip())

    def action_cancel(self) -> None:
        """Return to the previous interaction level. 返回上一交互层级。"""
        self.dismiss(None)
