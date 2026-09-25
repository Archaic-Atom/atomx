"""Message presentation without opaque assistant backgrounds. 消息渲染与背景处理。"""

from __future__ import annotations

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.markdown import CodeBlock
from rich.markdown import Markdown as RichMarkdown
from rich.padding import Padding
from rich.segment import Segment
from rich.style import Style
from rich.text import Text

from .appearance import Palette, palette_for, user_message_style
from .i18n import tr
from .state import clean


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
                yield Segment(segment.text, style, segment.control)


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


def pretty(
    item: dict, code_theme: str = "monokai", palette: Palette | None = None
) -> Group | Text | None:
    """Build one history item with role-specific presentation. 按角色渲染历史条目。"""
    palette = palette or palette_for({})
    kind = item.get("type", "")
    if kind == "userMessage":
        text = "\n".join(
            c.get("text", tr("[图片或附件]")) for c in item.get("content", [])
        )
        return Group(
            Padding(
                Text(tr("❯ 你\n") + clean(text)),
                (0, 1),
                style=user_message_style(palette),
            ),
            Text(""),
        )
    if kind in ("agentMessage", "plan"):
        return Group(
            Text(
                "✦ Codex" if kind == "agentMessage" else tr("◇ 计划"),
                style=palette.accent,
            ),
            MessageMarkdown(clean(item.get("text")), code_theme=code_theme),
            Text(""),
        )
    if kind == "commandExecution":
        status = item.get("status", "inProgress")
        mark = (
            "●"
            if status == "inProgress"
            else "✓"
            if status == "completed"
            else "!"
        )
        return Text(
            f"  {mark} {command_summary(item.get('command'))}",
            style=palette.muted,
            no_wrap=True,
            overflow="ellipsis",
        )
    if kind == "fileChange":
        paths = ", ".join(clean(c.get("path")) for c in item.get("changes", []))
        return Text(
            tr("  ◇ 文件修改 · {0}\n").format(paths), style=palette.muted
        )
    if kind == "mcpToolCall":
        return Text(
            f"  ◇ {clean(item.get('server'))} / {clean(item.get('tool'))}"
            f" · {clean(item.get('status'))}\n",
            style=palette.muted,
        )
    if kind in ("imageView", "imageGeneration"):
        return Text(
            "  ▧ " + tr("图片") + " · " + clean(item.get("status", "")),
            style=palette.accent,
        )
    if kind == "webSearch":
        return Text(
            tr("  ⌕ 搜索 · {0}\n").format(clean(item.get("query"))),
            style=palette.muted,
        )
    if kind == "notice":
        return Text(
            "! " + clean(item.get("text")) + "\n", style=palette.warning
        )
    return None
