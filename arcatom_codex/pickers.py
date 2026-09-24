"""Keyboard-first searchable choices. 可搜索的键盘选择面板。"""
from rich.text import Text
from textual import on, events
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


class PickerSearch(Input):
    async def _on_key(self, event: events.Key):
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            options = self.screen.query_one("#choices", OptionList)
            (options.action_cursor_up if event.key == "up" else options.action_cursor_down)()
        else:
            await super()._on_key(event)


class Picker(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title: str, choices: list[tuple[str, str]], selected: str | None = None):
        super().__init__()
        self.heading, self.choices, self.selected = title, choices, selected

    def compose(self):
        with Vertical(id="dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            yield PickerSearch(placeholder="输入筛选 · ↑↓ 选择 · Enter 确认 · Esc 取消", id="picker-search")
            yield OptionList(id="choices")

    def on_mount(self):
        self.populate("")
        self.query_one(Input).focus()

    def populate(self, query: str):
        options = self.query_one(OptionList)
        options.clear_options()
        for value, label in self.choices:
            if query.casefold() in (value + " " + label).casefold():
                options.add_option(Option(Text(("● " if value == self.selected else "  ") + label), id=value))
        if options.option_count:
            options.highlighted = 0
            if not query and self.selected:
                for i in range(options.option_count):
                    if options.get_option_at_index(i).id == self.selected:
                        options.highlighted = i
                        break

    @on(Input.Changed)
    def search(self, event):
        self.populate(event.value)

    @on(Input.Submitted)
    def submit(self):
        options = self.query_one(OptionList)
        if options.highlighted is not None:
            self.dismiss(options.get_option_at_index(options.highlighted).id)

    @on(OptionList.OptionSelected)
    def choose(self, event):
        self.dismiss(event.option.id)

    def action_cancel(self):
        self.dismiss(None)


class Prompt(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "取消")]

    def __init__(self, title: str, value: str = ""):
        super().__init__()
        self.heading, self.value = title, value

    def compose(self):
        with Vertical(id="small-dialog"):
            yield Static(Text(self.heading), id="dialog-title")
            yield Input(self.value)
            yield Static("Enter 确认 · Esc 取消")

    @on(Input.Submitted)
    def submit(self, event):
        if event.value.strip():
            self.dismiss(event.value.strip())

    def action_cancel(self):
        self.dismiss(None)
