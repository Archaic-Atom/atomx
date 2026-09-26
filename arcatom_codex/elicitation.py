"""MCP elicitation forms. MCP 扩展请求的结构化表单。"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import cast
from urllib.parse import urlparse

from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Select, SelectionList, Static, Switch

from .core.state import clean
from .i18n import tr

SELECT_EMPTY = getattr(Select, "NULL", Select.BLANK)


def form_fields(params: dict) -> list[tuple[str, dict]] | None:
    """Recognize the primitive schema supported by the local Codex protocol.

    识别本机 Codex 协议中的基础字段；未知结构不猜测提交。

    Args:
        params: Elicitation request parameters. 扩展表单请求参数。

    Returns:
        Ordered fields or None for unsupported schemas. 字段列表或不支持标志。
    """
    if params.get("mode") not in ("form", "openai/form", "openaiForm"):
        return None
    schema = params.get("requestedSchema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    properties = schema.get("properties")
    required = schema.get("required", [])
    if (
        not isinstance(properties, dict)
        or not isinstance(required, list)
        or not all(isinstance(name, str) for name in required)
    ):
        return None
    if any(key not in properties for key in required):
        return None
    fields: list[tuple[str, dict]] = []
    for name, field in properties.items():
        if not isinstance(name, str) or not isinstance(field, dict):
            return None
        kind = field.get("type")
        if kind in ("string", "number", "integer", "boolean"):
            if kind == "string" and ("enum" in field or "oneOf" in field):
                options = field.get("enum", field.get("oneOf"))
                if not isinstance(options, list) or not options:
                    return None
                if "oneOf" in field and not all(
                    isinstance(option, dict)
                    and isinstance(option.get("const"), str)
                    and isinstance(option.get("title"), str)
                    for option in options
                ):
                    return None
                if "enum" in field and not all(
                    isinstance(option, str) for option in options
                ):
                    return None
        elif kind == "array":
            items = field.get("items")
            if not isinstance(items, dict):
                return None
            options = items.get("enum", items.get("anyOf"))
            if not isinstance(options, list) or not options:
                return None
            if "anyOf" in items and not all(
                isinstance(option, dict)
                and isinstance(option.get("const"), str)
                and isinstance(option.get("title"), str)
                for option in options
            ):
                return None
            if "enum" in items and not all(
                isinstance(option, str) for option in options
            ):
                return None
        else:
            return None
        fields.append((name, field))
    return fields


class ElicitationForm(ModalScreen[dict]):
    """Collect one MCP form and return its explicit action. 收集表单并返回用户选择。"""

    BINDINGS = [("escape", "cancel", tr("取消"))]

    def __init__(self, params: dict) -> None:
        """Keep protocol input separate from visible controls. 保存协议字段定义。"""
        super().__init__()
        self.params = params
        self.fields = form_fields(params)
        schema = params.get("requestedSchema")
        self.required = (
            set(schema.get("required", []))
            if self.fields is not None and isinstance(schema, dict)
            else set()
        )

    def compose(self) -> ComposeResult:
        """Build a scrollable form with explicit decisions. 构建可滚动表单。"""
        title = clean(self.params.get("serverName") or tr("扩展表单"))
        with Vertical(id="dialog"):
            yield Static(Text(title), id="dialog-title")
            with VerticalScroll(id="elicitation-scroll"):
                yield Static(
                    clean(self.params.get("message")),
                    id="form-message",
                    markup=False,
                )
                if self.fields is None:
                    yield Static(tr("此扩展表单包含尚不支持的字段，无法提交。"))
                else:
                    for index, (name, field) in enumerate(self.fields):
                        label = clean(field.get("title") or name)
                        if name in self.required:
                            label += " *"
                        yield Static(
                            label, classes="setting-label", markup=False
                        )
                        if field.get("description"):
                            yield Static(
                                clean(field["description"]),
                                classes="setting-description muted",
                                markup=False,
                            )
                        kind = field["type"]
                        default = field.get("default")
                        if kind == "string" and (
                            "enum" in field or "oneOf" in field
                        ):
                            options = field.get("oneOf", field.get("enum", []))
                            choices = [
                                (option["title"], option["const"])
                                if isinstance(option, dict)
                                else (option, option)
                                for option in options
                            ]
                            yield Select(
                                choices,
                                prompt=tr("请选择"),
                                value=default
                                if default is not None
                                else SELECT_EMPTY,
                                id=f"field-{index}",
                            )
                        elif kind == "array":
                            items = field["items"]
                            options = items.get("anyOf", items.get("enum", []))
                            selected = (
                                default if isinstance(default, list) else []
                            )
                            yield SelectionList(
                                *[
                                    (
                                        option["title"],
                                        option["const"],
                                        option["const"] in selected,
                                    )
                                    if isinstance(option, dict)
                                    else (option, option, option in selected)
                                    for option in options
                                ],
                                id=f"field-{index}",
                            )
                        elif kind == "boolean":
                            yield Switch(
                                value=bool(default), id=f"field-{index}"
                            )
                        else:
                            yield Input(
                                value=str(default)
                                if default is not None
                                else "",
                                password=bool(
                                    field.get("isSecret")
                                    or field.get("writeOnly")
                                ),
                                id=f"field-{index}",
                            )
            yield Static("", id="form-error", markup=False)
            with Horizontal(id="dialog-actions"):
                if self.fields is not None:
                    yield Button(
                        tr("提交"), id="form-accept", variant="primary"
                    )
                yield Button(tr("拒绝"), id="form-decline")
                yield Button(tr("取消"), id="form-cancel")

    def on_mount(self) -> None:
        """Start on the first field when available. 默认聚焦第一个字段。"""
        first = self.query("#field-0")
        if first:
            first.first().focus()
        else:
            self.query_one("#form-decline", Button).focus()

    def collect(self) -> dict[str, object] | None:
        """Validate values before accepting. 提交前核对必填和字段约束。"""
        if self.fields is None:
            return None
        content: dict[str, object] = {}
        for index, (name, field) in enumerate(self.fields):
            kind = field["type"]
            control = self.query_one(f"#field-{index}")
            value: object
            if isinstance(control, Select):
                value = control.value
                if value is SELECT_EMPTY:
                    if name in self.required:
                        self.invalid(name, tr("此项为必填项"), control)
                        return None
                    continue
            elif isinstance(control, SelectionList):
                value = list(control.selected)
                minimum = field.get(
                    "minItems", 1 if name in self.required else 0
                )
                maximum = field.get("maxItems")
                if len(value) < minimum or (
                    maximum is not None and len(value) > maximum
                ):
                    self.invalid(name, tr("所选数量不符合要求"), control)
                    return None
            elif isinstance(control, Switch):
                value = control.value
            else:
                raw = cast(Input, control).value
                if not raw and name not in self.required:
                    continue
                if kind == "string":
                    if len(raw) < field.get("minLength", 0) or len(
                        raw
                    ) > field.get("maxLength", float("inf")):
                        self.invalid(name, tr("文字长度不符合要求"), control)
                        return None
                    if not self.valid_format(raw, field.get("format")):
                        self.invalid(name, tr("输入格式不正确"), control)
                        return None
                    value = raw
                else:
                    try:
                        value = int(raw) if kind == "integer" else float(raw)
                        if kind == "number" and (
                            value != value
                            or value in (float("inf"), float("-inf"))
                        ):
                            raise ValueError
                    except (ValueError, OverflowError):
                        self.invalid(name, tr("请输入有效数字"), control)
                        return None
                    if value < field.get(
                        "minimum", float("-inf")
                    ) or value > field.get("maximum", float("inf")):
                        self.invalid(name, tr("数字超出允许范围"), control)
                        return None
            content[name] = value
        return content

    @staticmethod
    def valid_format(value: str, format_name: str | None) -> bool:
        """Check advertised string formats. 检查声明的字符串格式。"""
        if format_name == "email":
            return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))
        if format_name == "uri":
            parsed = urlparse(value)
            return bool(parsed.scheme and (parsed.netloc or parsed.path))
        if format_name in ("date", "date-time"):
            try:
                if format_name == "date":
                    date.fromisoformat(value)
                else:
                    datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
        return True

    def invalid(self, name: str, reason: str, control: object) -> None:
        """Show the failing field without losing entered values. 标出错误并保留输入。"""
        self.query_one("#form-error", Static).update(f"{name}: {reason}")
        cast(Input | Select | SelectionList | Switch, control).focus()

    @on(Button.Pressed)
    def decide(self, event: Button.Pressed) -> None:
        """Return only the user's explicit choice. 只返回用户明确选择的结果。"""
        if event.button.id == "form-accept":
            content = self.collect()
            if content is not None:
                self.dismiss(
                    {"action": "accept", "content": content, "_meta": None}
                )
        else:
            action = (
                "decline" if event.button.id == "form-decline" else "cancel"
            )
            self.dismiss({"action": action, "content": None, "_meta": None})

    def action_cancel(self) -> None:
        """Cancel on Escape. Esc 取消。"""
        self.dismiss({"action": "cancel", "content": None, "_meta": None})


class ElicitationLink(ModalScreen[dict]):
    """Let the user complete a URL elicitation externally. 让用户自行完成链接验证。"""

    BINDINGS = [("escape", "cancel", tr("取消"))]

    def __init__(self, params: dict) -> None:
        """Retain the link without opening it automatically. 保存链接但不自动打开。"""
        super().__init__()
        self.params = params
        self.url = str(params.get("url") or "")

    def compose(self) -> ComposeResult:
        """Show the request and an explicit completion choice. 显示请求和完成选项。"""
        with Vertical(id="dialog"):
            yield Static(
                Text(clean(self.params.get("serverName") or tr("扩展链接"))),
                id="dialog-title",
            )
            with VerticalScroll(id="elicitation-scroll"):
                yield Static(clean(self.params.get("message")), markup=False)
                yield Static(clean(self.url), markup=False)
                yield Static(tr("复制链接并在浏览器完成操作，然后选择已完成。"))
            with Horizontal(id="dialog-actions"):
                yield Button(tr("复制链接"), id="link-copy")
                yield Button(tr("已完成"), id="link-accept")
                yield Button(tr("拒绝"), id="link-decline")
                yield Button(tr("取消"), id="link-cancel")

    def on_mount(self) -> None:
        """Make copying the first action. 默认选中复制链接。"""
        self.query_one("#link-copy", Button).focus()

    @on(Button.Pressed)
    def decide(self, event: Button.Pressed) -> None:
        """Respond only after an explicit choice. 只在明确选择后回复后端。"""
        button = event.button.id
        if button == "link-copy":
            self.app.copy_to_clipboard(self.url)
            return
        action = {
            "link-accept": "accept",
            "link-decline": "decline",
        }.get(button or "", "cancel")
        self.dismiss({"action": action, "content": None, "_meta": None})

    def action_cancel(self) -> None:
        """Cancel on Escape. Esc 取消。"""
        self.dismiss({"action": "cancel", "content": None, "_meta": None})
