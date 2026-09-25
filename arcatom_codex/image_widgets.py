"""Inline images with selectable surrounding text. 会话图片与可复制文字分开渲染。"""

from __future__ import annotations

import asyncio
import subprocess
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PILImage
from rich.console import RenderableType
from textual import events
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Static
from textual_image._terminal import get_cell_size
from textual_image.widget import HalfcellImage
from textual_image.widget import Image as TerminalImage
from textual_image.widget._base import Image as BaseImage

from .access import WorkspaceAccess
from .i18n import tr
from .images import ImageSource, load_image, original_image_path
from .platform_support import open_image_file
from .state import clean
from .widgets import SelectableTranscript


class ImageLoader:
    """Bound decoding concurrency and retain recent thumbnails. 限制解码并发并缓存缩略图。"""

    def __init__(self) -> None:
        """Create a cache owned by this app instance. 每个应用单独保存图片缓存。"""
        self.cache: OrderedDict[str, PILImage.Image] = OrderedDict()
        self.semaphore = asyncio.Semaphore(2)

    async def load(self, source: ImageSource) -> PILImage.Image:
        """Decode without blocking input or re-reading streamed messages.

        后台解码，同一图片不随回复流重复读取。
        """
        async with self.semaphore:
            if not source.uri.startswith("data:"):
                # A tool may overwrite a local plot at the same path.
                # 工具可能覆盖同一路径的图片，新建预览时应重新读取。
                return await asyncio.to_thread(load_image, source)
            key = source.key
            if key not in self.cache:
                self.cache[key] = await asyncio.to_thread(load_image, source)
                while len(self.cache) > 16:
                    self.cache.popitem(last=False)
            self.cache.move_to_end(key)
            return self.cache[key]


class ImagePreview(WorkspaceAccess, Vertical, can_focus=False):
    """Render a bounded image without participating in text selection.

    图片独立绘制，避免图片控制码进入文字选区。
    """

    DEFAULT_CSS = """
    ImagePreview { height: auto; width: 100%; margin-bottom: 1; }
    ImagePreview > .image-caption { height: auto; color: $text-muted; }
    ImagePreview > .image-pixels { width: auto; height: auto; }
    """

    def __init__(self, source: ImageSource, loader: ImageLoader) -> None:
        """Keep only source metadata until mounted. 挂载前只保存图片引用。"""
        super().__init__()
        self.source = source
        self.loader = loader
        self.pixels: BaseImage | None = None
        self.decoded: PILImage.Image | None = None
        self.thread_id: str | None = None

    def compose(self) -> ComposeResult:
        """Reserve a labelled position while decoding. 解码时显示有名称的占位。"""
        yield Static(
            tr("▧ {0} · 正在加载图片…").format(clean(self.source.caption)),
            classes="image-caption",
            markup=False,
        )

    def on_mount(self) -> None:
        """Begin an owned cancellable load. 启动随控件关闭而取消的加载。"""
        self.thread_id = self.workspace.current
        self.run_worker(self.load_preview(), exit_on_error=False)

    async def load_preview(self) -> None:
        """Show pixels or a readable failure without disrupting the chat.

        展示图片或明确的错误，不影响会话其余内容。
        """
        caption = self.query_one(".image-caption", Static)
        scroll = self.workspace.query_one("#transcript-scroll", VerticalScroll)
        try:
            self.decoded = await self.loader.load(self.source)
            if not self.is_mounted:
                return
            # Headless tests use real colored pixels without terminal probing.
            # 无终端测试仍绘制彩色像素，不发送终端图像控制码。
            image_type = (
                HalfcellImage if self.app.is_headless else TerminalImage
            )
            self.pixels = image_type(self.decoded, classes="image-pixels")
            follow = scroll.is_vertical_scroll_end and not scroll.has_focus
            await self.mount(self.pixels)
            self.fit_image()
            caption.update("▧ " + clean(self.source.caption))
            if follow and self.workspace.view_preferences.get(
                "follow_output", True
            ):
                self.call_after_refresh(self.follow_if_current)
        except (OSError, ValueError, PILImage.DecompressionBombError) as exc:
            caption.update(
                tr("▧ {0} · 无法预览：{1}").format(
                    clean(self.source.caption), clean(str(exc))
                )
            )

    def follow_if_current(self) -> None:
        """Follow only while this preview is visible and browsing has not begun.

        只在当前会话仍显示且用户没有开始浏览时跟随。
        """
        scroll = self.workspace.query_one("#transcript-scroll", VerticalScroll)
        if (
            self.is_on_screen
            and self.thread_id == self.workspace.current
            and not scroll.has_focus
        ):
            scroll.scroll_end(animate=False, immediate=True)

    def on_resize(self) -> None:
        """Fit the source aspect ratio to the new terminal width. 缩放后保持原始比例。"""
        self.fit_image()

    def fit_image(self) -> None:
        """Limit image height while preserving rectangular pixels. 限高并保持图片比例。"""
        if self.pixels is None or self.decoded is None:
            return
        cell = get_cell_size()
        width = min(96, max(1, self.content_size.width))
        scale = min(
            width * cell.width / self.decoded.width,
            24 * cell.height / self.decoded.height,
            1.0,
        )
        self.pixels.styles.width = max(
            1, round(self.decoded.width * scale / cell.width)
        )
        self.pixels.styles.height = max(
            1, round(self.decoded.height * scale / cell.height)
        )

    def on_unmount(self) -> None:
        """Release terminal image resources when history is replaced. 移除时释放终端图像。"""
        if self.pixels is not None:
            self.pixels.image = None

    def on_click(self, event: events.Click) -> None:
        """Open the original preview on explicit click. 点击图片后进入大图查看器。"""
        event.stop()
        self.workspace.action_view_images(self.source)


class ImageGallery(ModalScreen[None]):
    """Browse and zoom images without leaving the terminal. 在终端内切换和放大图片。"""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("left", "previous", show=False, priority=True),
        Binding("right", "next", show=False, priority=True),
        Binding("plus,equals", "zoom(1.5)", show=False),
        Binding("minus", "zoom(0.6666667)", show=False),
        Binding("0", "fit", show=False),
        Binding("o", "open_original", show=False),
    ]
    DEFAULT_CSS = """
    ImageGallery > #image-dialog { width: 95%; height: 95%; border: round $accent; }
    #image-heading, #image-help { height: auto; padding: 0 1; }
    #image-viewport { height: 1fr; overflow: auto auto; }
    """

    def __init__(
        self, sources: list[ImageSource], index: int, loader: ImageLoader
    ) -> None:
        """Retain gallery sources and share decoded previews. 复用已解码图片缓存。"""
        super().__init__()
        self.sources, self.index, self.loader = sources, index, loader
        self.zoom = 1.0
        self.decoded: PILImage.Image | None = None
        self.pixels: BaseImage | None = None

    def compose(self) -> ComposeResult:
        """Build the image viewport and keyboard help. 构造图片视口及键盘提示。"""
        with Vertical(id="image-dialog"):
            yield Static("", id="image-heading", markup=False)
            yield VerticalScroll(id="image-viewport")
            yield Static(
                tr("←/→ 切换 · +/- 缩放 · 0 适应窗口 · O 打开原图 · Esc 返回"),
                id="image-help",
            )

    def on_mount(self) -> None:
        """Load the selected image after the viewport exists. 挂载后读取选中的图片。"""
        self.query_one("#image-viewport").focus()
        self.show_image()

    def show_image(self) -> None:
        """Cancel stale reads when moving between images. 切换图片时取消旧加载。"""
        self.run_worker(
            self.load_current(),
            group="gallery",
            exclusive=True,
            exit_on_error=False,
        )

    async def load_current(self) -> None:
        """Load a page without freezing keyboard navigation. 后台读取而不阻塞导航。"""
        source = self.sources[self.index]
        heading = self.query_one("#image-heading", Static)
        heading.update(
            f"{self.index + 1}/{len(self.sources)} · {clean(source.caption)}"
        )
        viewport = self.query_one("#image-viewport", VerticalScroll)
        if self.pixels:
            self.pixels.image = None
        self.pixels = None
        self.decoded = None
        await viewport.remove_children()
        try:
            self.decoded = await self.loader.load(source)
            image_type = (
                HalfcellImage if self.app.is_headless else TerminalImage
            )
            self.pixels = image_type(self.decoded)
            await viewport.mount(self.pixels)
            viewport.scroll_home(animate=False)
            self.fit_image()
        except (OSError, ValueError, PILImage.DecompressionBombError) as exc:
            heading.update(
                tr("▧ {0} · 无法预览：{1}").format(
                    clean(source.caption), clean(str(exc))
                )
            )

    def fit_image(self) -> None:
        """Scale proportionally, allowing scrollable zoom. 按比例缩放，放大后支持滚动。"""
        if self.pixels is None or self.decoded is None:
            return
        viewport = self.query_one("#image-viewport")
        cell = get_cell_size()
        ratio = min(
            max(1, viewport.content_size.width - 1)
            * cell.width
            / self.decoded.width,
            max(1, viewport.content_size.height - 1)
            * cell.height
            / self.decoded.height,
        )
        scale = min(
            ratio * self.zoom,
            250 * cell.width / self.decoded.width,
            250 * cell.height / self.decoded.height,
        )
        self.pixels.styles.width = max(
            1, round(self.decoded.width * scale / cell.width)
        )
        self.pixels.styles.height = max(
            1, round(self.decoded.height * scale / cell.height)
        )

    def on_resize(self) -> None:
        """Recalculate fit after layout settles. 布局完成后重算适应大小。"""
        self.call_after_refresh(self.fit_image)

    def action_previous(self) -> None:
        """Select the preceding image. 查看上一张图片。"""
        self.index = (self.index - 1) % len(self.sources)
        self.zoom = 1.0
        self.show_image()

    def action_next(self) -> None:
        """Select the next image. 查看下一张图片。"""
        self.index = (self.index + 1) % len(self.sources)
        self.zoom = 1.0
        self.show_image()

    def action_zoom(self, factor: float) -> None:
        """Adjust bounded zoom. 调整有上限的缩放倍数。"""
        self.zoom = max(0.25, min(4.0, self.zoom * factor))
        self.fit_image()

    def action_fit(self) -> None:
        """Restore viewport fitting. 恢复适应窗口。"""
        self.zoom = 1.0
        self.fit_image()

    def action_close(self) -> None:
        """Return to the same conversation and draft. 返回原会话及草稿。"""
        self.dismiss(None)

    async def action_open_original(self) -> None:
        """Open the original only after an explicit O key. 按 O 后才调用系统原图查看器。"""
        try:
            path = await asyncio.to_thread(
                original_image_path,
                self.sources[self.index],
                Path.home() / ".cache" / "arcatom" / "attachments",
            )
            await asyncio.to_thread(open_image_file, path)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            self.notify(clean(str(exc)), severity="error")

    def on_unmount(self) -> None:
        """Release native image placements. 释放终端图像资源。"""
        if self.pixels:
            self.pixels.image = None


@dataclass
class MediaBlock:
    """One stable text or image block. 一个有稳定标识的文字或图片块。"""

    key: str
    signature: str
    content: RenderableType | ImageSource


class MediaTranscript(WorkspaceAccess, Vertical):
    """Incrementally reconcile media without remounting every streamed delta.

    增量更新图文日志，流式回复不重复挂载或解码已有图片。
    """

    DEFAULT_CSS = """
    MediaTranscript { height: auto; padding-right: 1; }
    MediaTranscript > SelectableTranscript { height: auto; }
    """

    def __init__(self, *, id: str) -> None:
        """Initialize the per-view reconciliation state. 初始化视图增量更新状态。"""
        super().__init__(id=id)
        self.loader = ImageLoader()
        self.desired: list[MediaBlock] = []
        self.mounted_blocks: dict[str, tuple[str, Widget]] = {}
        self.syncing = False
        self.generation = 0
        self.follow = False

    def update_blocks(
        self, blocks: list[MediaBlock], follow: bool = False
    ) -> None:
        """Queue the latest view without concurrent tree mutations. 串行处理最新视图。"""
        self.desired = blocks
        self.follow = follow
        self.generation += 1
        if not self.syncing:
            self.syncing = True
            self.run_worker(self.sync_blocks(), exit_on_error=False)

    async def sync_blocks(self) -> None:
        """Reuse stable widgets and preserve order through paging/session changes.

        分页和切换会话时保持顺序，保留未变化的控件。
        """
        try:
            while True:
                generation, blocks = self.generation, self.desired
                wanted = {block.key for block in blocks}
                for key in list(self.mounted_blocks):
                    if key not in wanted:
                        _, widget = self.mounted_blocks.pop(key)
                        await widget.remove()
                for index, block in enumerate(blocks):
                    cached = self.mounted_blocks.get(block.key)
                    if cached is None:
                        widget = (
                            ImagePreview(block.content, self.loader)
                            if isinstance(block.content, ImageSource)
                            else SelectableTranscript(
                                block.content, markup=False
                            )
                        )
                        await self.mount(widget)
                    else:
                        old_signature, widget = cached
                        if old_signature != block.signature and isinstance(
                            widget, Static
                        ):
                            assert not isinstance(block.content, ImageSource)
                            widget.update(block.content)
                    self.mounted_blocks[block.key] = (block.signature, widget)
                    if self.children[index] is not widget:
                        self.move_child(widget, before=self.children[index])
                if generation == self.generation:
                    if self.follow:
                        self.call_after_refresh(self.follow_output, generation)
                    break
        finally:
            self.syncing = False

    def follow_output(self, generation: int) -> None:
        """Anchor new output only after its layout completes. 图文布局完成后再跟随。"""
        scroll = self.workspace.query_one("#transcript-scroll", VerticalScroll)
        if (
            self.is_on_screen
            and generation == self.generation
            and not scroll.has_focus
        ):
            scroll.scroll_end(animate=False, immediate=True)
