"""Copy complete responses and browse conversation images. 复制整条回复及浏览会话图片。"""

from __future__ import annotations

from markdown_it import MarkdownIt
from rich.cells import cell_len
from rich.console import Console

from .access import AppActions
from .i18n import tr
from .image_widgets import ImageGallery, MediaTranscript
from .images import ImageSource, message_images
from .pickers import Picker
from .rendering import MessageMarkdown
from .state import clean


def response_formats(text: str) -> list[tuple[str, str, str]]:
    """Offer source Markdown, readable text and individual code blocks.

    提供原始 Markdown、可读全文以及保持原始缩进的独立代码块。
    """
    width = max(
        80, max((cell_len(line) for line in text.splitlines()), default=0) + 8
    )
    console = Console(width=width, color_system=None)
    lines = []
    for segment in console.render(MessageMarkdown(text)):
        if not segment.control and not (
            segment.style and segment.style.meta.get("atomx_copy_padding")
        ):
            lines.append(segment.text)
    plain = "\n".join(
        line.rstrip() for line in "".join(lines).splitlines()
    ).strip("\n")
    choices = [
        ("markdown", tr("整条回答 · 原始 Markdown"), text),
        ("plain", tr("整条回答 · 纯文本"), plain),
    ]
    for token in MarkdownIt().parse(text):
        if token.type in ("fence", "code_block"):
            index = len(choices) - 1
            label = tr("代码块 {0} · {1}").format(
                index, token.info.strip() or "text"
            )
            choices.append((f"code-{index}", label, token.content))
    return choices


class ResponseActions(AppActions):
    """Keep response tools separate from navigation and turn handling.

    回复工具独立于导航及任务控制。
    """

    def action_copy_response(self) -> None:
        """Choose a reply and copy format with F5. F5 选择回复及复制格式。"""
        app = self.workspace
        if app.screen is not app.main_screen or not app.current:
            return
        replies = [
            item
            for item in reversed(
                list(app.store.get(app.current).items.values())
            )
            if item.get("type") == "agentMessage" and item.get("text")
        ]
        if not replies:
            app.notify(tr("当前没有可复制的回答。"))
            return

        async def choose() -> None:
            """Snapshot the reply so streamed updates cannot alter the selection.

            使用当前回复快照，避免流式更新改变待复制内容。
            """
            snapshots = [str(item["text"]) for item in replies]
            key = await app.push_screen_wait(
                Picker(
                    tr("复制回答 · 最新在前"),
                    [
                        (str(i), clean(text).replace("\n", " ")[:100])
                        for i, text in enumerate(snapshots)
                    ],
                )
            )
            if key is None:
                return
            formats = response_formats(snapshots[int(key)])
            selected = await app.push_screen_wait(
                Picker(
                    tr("选择复制格式"),
                    [(key, label) for key, label, _ in formats],
                )
            )
            if selected is not None:
                app.copy_to_clipboard(
                    next(value for key, _, value in formats if key == selected)
                )

        app.launch(choose())

    def action_view_images(self, selected: ImageSource | None = None) -> None:
        """Open a keyboard-operated image gallery. 打开支持键盘操作的图片查看器。"""
        app = self.workspace
        if app.screen is not app.main_screen or not app.current:
            return
        session = app.store.get(app.current)
        cwd = session.meta.get("cwd") or app.cwd
        sources = [
            source
            for item in session.items.values()
            for source in message_images(item, cwd)
        ]
        sources.extend(ImageSource(path, cwd) for path in session.attachments)
        sources = list({source.key: source for source in sources}.values())
        if not sources:
            app.notify(tr("当前会话没有图片。"))
            return
        index = next(
            (
                i
                for i, source in enumerate(sources)
                if selected and source.key == selected.key
            ),
            len(sources) - 1,
        )
        loader = app.query_one("#media-transcript", MediaTranscript).loader
        app.push_screen(ImageGallery(sources, index, loader))
