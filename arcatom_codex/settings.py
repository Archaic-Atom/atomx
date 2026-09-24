"""Keyboard accessible settings with reversible previews. 可取消的实时设置预览。"""
from .i18n import tr
from pathlib import Path

from rich.text import Text
from textual import on
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, Static, Switch

from .appearance import ACCENTS, PALETTES, palette_for


class Settings(ModalScreen[dict | None]):
    """Edit a copy; the application persists only on Save. 保存前只预览。"""

    BINDINGS = [("escape", "cancel", tr('取消')), ("ctrl+s", "save", tr('保存'))]

    def __init__(self, preferences: dict, cwd: str):
        super().__init__()
        self.values = dict(preferences)
        self.cwd = cwd

    def compose(self):
        with Vertical(id="dialog"):
            yield Static(tr('设置 · 主题与使用习惯'), id="dialog-title")
            with VerticalScroll(id="settings-scroll"):
                yield Static(tr("语言 / Language"), classes="setting-label")
                yield Select([("English", "en"), ("简体中文", "zh")], id="language",
                             value=self.values.get("language", "en"), allow_blank=False)
                yield Static(tr('界面调色板 · 首页和会话同步预览'), classes="setting-label")
                yield Select([(Text("██  " + tr(p.label), p.accent), key)
                              for key, p in PALETTES.items()], id="ui-theme",
                             value=self.values.get("ui_theme", "warm"), allow_blank=False)
                yield Static(tr('强调色'), classes="setting-label")
                yield Select([(Text("●  " + tr(label), palette_for({**self.values, "accent": key}).accent), key)
                              for key, label in ACCENTS.items()], id="accent",
                             value=self.values.get("accent", "default"), allow_blank=False)
                yield Static("", id="palette-preview", markup=False)
                with Horizontal(classes="setting-row"):
                    yield Static(tr('自动跟随最新输出（上翻后暂停）'))
                    yield Switch(self.values.get("follow_output", True), id="follow-output")
                with Horizontal(classes="setting-row"):
                    yield Static(tr('始终使用紧凑布局'))
                    yield Switch(self.values.get("compact", False), id="compact-layout")
                yield Static(tr('新会话默认目录 · 留空沿用当前目录'), classes="setting-label")
                yield Input(self.values.get("default_cwd", ""), placeholder=self.cwd,
                            id="default-cwd")
                yield Static(tr('更多：/model 模型 · /statusline 状态栏 · /theme 代码高亮'),
                             classes="muted")
            with Horizontal(id="dialog-actions"):
                yield Button(tr('保存 · Ctrl+S'), id="save-settings", variant="primary")
                yield Button(tr('取消 · Esc'), id="cancel-settings")

    def on_mount(self):
        self.preview()
        self.query_one("#language").focus()

    def preview(self):
        """Apply semantic colors without writing the preference file. 仅预览。"""
        palette = palette_for(self.values)
        self.app.apply_appearance(self.values)
        text = Text(tr('  正文  '), style=palette.foreground + " on " + palette.background)
        text.append(tr('  你的消息  '), "bold " + palette.foreground + " on " + palette.user)
        text.append(tr('  强调色  '), palette.accent)
        self.query_one("#palette-preview", Static).update(text)

    @on(Select.Changed)
    def selection_changed(self, event):
        if isinstance(event.value, str):
            if event.select.id == "language":
                self.values["language"] = event.value
                return
            self.values[{"ui-theme": "ui_theme", "accent": "accent"}[event.select.id]] = event.value
            self.preview()

    @on(Switch.Changed)
    def switch_changed(self, event):
        key = {"follow-output": "follow_output", "compact-layout": "compact"}[event.switch.id]
        self.values[key] = event.value
        self.preview()

    def action_save(self):
        directory = self.query_one("#default-cwd", Input).value.strip()
        if directory:
            path = Path(directory).expanduser().resolve()
            if not path.is_dir():
                self.notify(tr('默认目录不存在，请修改或留空。'), severity="warning")
                self.query_one("#default-cwd").focus()
                return
            directory = str(path)
        self.values["default_cwd"] = directory
        self.dismiss(self.values)

    def action_cancel(self):
        self.dismiss(None)

    @on(Button.Pressed, "#save-settings")
    def save_button(self):
        self.action_save()

    @on(Button.Pressed, "#cancel-settings")
    def cancel_button(self):
        self.action_cancel()
