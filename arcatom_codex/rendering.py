"""Message presentation without opaque assistant backgrounds. 消息渲染与背景处理。"""

from __future__ import annotations

import json

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock
from rich.markdown import Markdown as RichMarkdown
from rich.padding import Padding
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from .appearance import Palette, palette_for, user_message_style
from .core.state import clean
from .i18n import tr

COMPACT_ACTIVITY_TYPES = frozenset(
    {
        "commandExecution",
        "fileChange",
        "mcpToolCall",
        "webSearch",
        "dynamicToolCall",
        "collabAgentToolCall",
        "imageView",
        "imageGeneration",
    }
)
COMMAND_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class CopyableCodeBlock(CodeBlock):
    """Identify decorative code padding without changing its appearance.

    标记代码块的显示边距；复制时排除边距，屏幕外观保持不变。
    """

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        """Mark only the first padding cell, keeping source indentation.

        只标记每行最前方的一格显示边距，保留代码自身缩进。
        """
        for renderable in super().__rich_console__(console, options):
            if isinstance(renderable, Segment):
                yield renderable
                continue
            for line in console.render_lines(renderable, options, pad=False):
                first = True
                for segment in line:
                    if first and segment.text and not segment.control:
                        first = False
                        if segment.text.startswith(" "):
                            yield Segment(
                                " ",
                                (segment.style or Style())
                                + Style(meta={"atomx_copy_padding": True}),
                            )
                            yield Segment(segment.text[1:], segment.style)
                            continue
                    yield segment
                yield Segment.line()


class MessageMarkdown(RichMarkdown):
    """Keep syntax colors without opaque code backgrounds. 代码保留彩色，去掉黑底。"""

    elements = {
        **RichMarkdown.elements,
        "fence": CopyableCodeBlock,
        "code_block": CopyableCodeBlock,
    }

    def __init__(
        self,
        markup: str,
        code_theme: str = "monokai",
        link_color: str = "#e8a0b1",
    ) -> None:
        """Set a readable rose for clickable links. 为链接设置清晰的玫红色。

        Args:
            markup: Markdown source. Markdown 原文。
            code_theme: Code highlighting theme. 代码高亮主题。
            link_color: Theme-aware link foreground. 随主题调整的链接前景色。
        """
        super().__init__(markup, code_theme=code_theme)
        self.link_color = link_color

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        """Strip only backgrounds from rendered segments. 只移除渲染片段的背景。"""
        for part in super().__rich_console__(console, options):
            segments = (
                (part,)
                if isinstance(part, Segment)
                else console.render(part, options)
            )
            for segment in segments:
                style = segment.style
                if style and style.bgcolor is not None:
                    # Textual converts Rich's default background to RGB. Omit it instead.
                    # 默认底色会在可复制文本转换时变成实色，因此彻底去掉底色属性。
                    style = style.without_color + Style(
                        color=style.color, meta=style.meta
                    )
                if style and style.link:
                    style = style + Style(color=self.link_color, underline=True)
                yield Segment(segment.text, style, segment.control)


def format_elapsed(seconds: float) -> str:
    """Format elapsed seconds from the largest unit down to seconds.

    按天、小时、分钟、秒显示耗时，省略开头为零的单位。

    Args:
        seconds: Elapsed time in seconds. 以秒为单位的耗时。

    Returns:
        Duration such as "1h 0m 5s". 例如 "1h 0m 5s"。
    """
    remaining = max(0, int(seconds))
    parts: list[str] = []
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m"), (1, "s")):
        value, remaining = divmod(remaining, size)
        if value or parts or unit == "s":
            parts.append(f"{value}{unit}")
    return " ".join(parts)


def command_summary(command: str | None) -> str:
    """Summarize a command without its script body. 命令摘要不展开脚本正文。

    Args:
        command: Original shell command. 原始 shell 命令。

    Returns:
        First line with an omission marker when needed. 首行及省略标记。
    """
    lines = clean(command).strip().splitlines()
    if not lines:
        return tr("执行命令")
    return lines[0].strip().expandtabs(4) + (" …" if len(lines) > 1 else "")


def activity_request(item: dict) -> str:
    """Describe the request side of an inline activity, excluding its result.

    提取活动的请求内容，不把执行过程或结果放进会话正文。

    Args:
        item: Codex activity item. Codex 活动条目。

    Returns:
        Readable request text. 可读的请求内容。
    """
    kind = item.get("type")
    if kind == "commandExecution":
        return clean(item.get("command"))
    if kind == "webSearch":
        return clean(item.get("query"))
    if kind == "fileChange":
        return "\n".join(
            clean(change.get("path")) for change in item.get("changes", [])
        )
    if kind in ("mcpToolCall", "dynamicToolCall"):
        arguments = item.get("arguments") or item.get("input")
        if isinstance(arguments, str):
            return arguments
        if arguments is not None:
            return json.dumps(arguments, ensure_ascii=False, indent=2)
        return ""
    if kind == "collabAgentToolCall":
        return clean(item.get("prompt")) or "\n".join(
            clean(tid) for tid in item.get("receiverThreadIds") or []
        )
    if kind in ("imageView", "imageGeneration"):
        return clean(item.get("path") or item.get("savedPath"))
    return ""


def activity_label(item: dict) -> str:
    """Build one-line activity text. 构造活动单行标题。"""
    kind = item.get("type")
    if kind == "commandExecution":
        return command_summary(item.get("command"))
    if kind == "webSearch":
        return tr("搜索 · {0}").format(command_summary(item.get("query")))
    if kind == "fileChange":
        paths = ", ".join(clean(c.get("path")) for c in item.get("changes", []))
        return tr("文件修改 · {0}").format(paths)
    if kind == "mcpToolCall":
        return f"{clean(item.get('server'))} / {clean(item.get('tool'))}"
    if kind == "dynamicToolCall":
        return "/".join(
            filter(
                None, (clean(item.get("namespace")), clean(item.get("tool")))
            )
        )
    if kind == "collabAgentToolCall":
        return tr("子代理 · {0} · {1} 个").format(
            clean(item.get("tool")), len(item.get("receiverThreadIds") or [])
        )
    return tr("图片")


def pretty(
    item: dict,
    code_theme: str = "monokai",
    palette: Palette | None = None,
    compact_after: bool = False,
    expanded: bool = False,
    spinner_frame: int = 0,
    command_arrow: str | None = None,
) -> Group | Text | None:
    """Build one history item with role-specific presentation. 按角色渲染历史条目。"""
    palette = palette or palette_for({})
    kind = item.get("type", "")
    if kind == "userMessage":
        text = "\n".join(
            c.get("text", tr("[图片或附件]")) for c in item.get("content", [])
        )
        spacer = () if compact_after else (Text(""),)
        return Group(
            Padding(
                Text(tr("❯ 你\n") + clean(text)),
                (0, 1),
                style=user_message_style(palette),
            ),
            *spacer,
        )
    if kind in ("agentMessage", "plan"):
        spacer = () if compact_after else (Text(""),)
        return Group(
            Text(
                "✦ Codex" if kind == "agentMessage" else tr("◇ 计划"),
                style=palette.accent,
            ),
            MessageMarkdown(
                clean(item.get("text")),
                code_theme=code_theme,
                link_color=palette.link,
            ),
            *spacer,
        )
    if kind in COMPACT_ACTIVITY_TYPES:
        status = item.get(
            "status", "inProgress" if kind == "commandExecution" else None
        )
        mark = (
            COMMAND_SPINNER[spinner_frame % len(COMMAND_SPINNER)]
            if status in ("inProgress", "running")
            else "✓"
            if status == "completed"
            else "!"
            if status in ("failed", "error", "declined")
            else "⌕"
            if kind == "webSearch"
            else "▧"
            if kind in ("imageView", "imageGeneration")
            else "◇"
        )
        summary = Text(
            f"  {mark} {command_arrow or ('▾' if expanded else '>')} "
            + activity_label(item),
            style=Style(
                color=palette.muted,
                meta={"atomx_activity_id": str(item.get("id") or "")},
            ),
            no_wrap=True,
            overflow="ellipsis",
        )
        if not expanded:
            return summary
        request = activity_request(item)
        return (
            Group(summary, Text(request, style=palette.muted))
            if request
            else summary
        )
    if kind == "notice":
        return Text(
            "! " + clean(item.get("text")) + "\n", style=palette.warning
        )
    return None
