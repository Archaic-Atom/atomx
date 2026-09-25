"""Normalize and decode conversation images. 归一化并解码会话图片。"""

from __future__ import annotations

import base64
import hashlib
import io
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from urllib.parse import unquote, urlsplit

from markdown_it import MarkdownIt
from PIL import Image, ImageOps

from .i18n import tr

MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff"}


@dataclass(frozen=True)
class ImageSource:
    """Keep binary payloads separate from visible labels. 图片数据与显示标签分离。"""

    uri: str
    cwd: str
    label: str = ""

    @cached_property
    def key(self) -> str:
        """Identify an image without copying base64 into widget IDs. 图片键不暴露数据。"""
        return hashlib.sha256((self.cwd + "\0" + self.uri).encode()).hexdigest()

    @property
    def caption(self) -> str:
        """Provide a short label, never the encoded image. 返回简短标签而非图片编码。"""
        if self.label:
            return self.label
        if self.uri.startswith("data:"):
            return tr("图片")
        return Path(unquote(urlsplit(self.uri).path)).name or tr("图片")


def message_images(item: dict, cwd: str) -> list[ImageSource]:
    """Extract explicit image references from protocol items and Markdown.

    只提取协议图片字段及 Markdown 图片引用，不把任意工具文字当作路径。
    """
    found: list[ImageSource] = []

    def add(uri: object, label: str = "") -> None:
        """Record one usable reference. 保存有效图片引用。"""
        if isinstance(uri, str) and uri:
            found.append(ImageSource(uri, cwd, label))

    def content(parts: object) -> None:
        """Read known typed content containers. 读取已知的结构化内容。"""
        if not isinstance(parts, list):
            return
        for part in parts:
            if not isinstance(part, dict):
                continue
            kind = part.get("type")
            if kind == "localImage":
                add(part.get("path"))
            elif kind in ("image", "input_image", "inputImage"):
                uri = (
                    part.get("url")
                    or part.get("image_url")
                    or part.get("imageUrl")
                )
                if isinstance(uri, dict):
                    uri = uri.get("url")
                if not uri and isinstance(part.get("data"), str):
                    uri = f"data:{part.get('mimeType', 'image/png')};base64,{part['data']}"
                add(
                    uri
                    or (
                        "unavailable:"
                        + str(
                            part.get("fileId") or part.get("file_id") or "image"
                        )
                    )
                )
            elif kind == "resource":
                resource = part.get("resource", {})
                if isinstance(resource, dict) and str(
                    resource.get("mimeType", "")
                ).startswith("image/"):
                    blob = resource.get("blob")
                    add(
                        f"data:{resource['mimeType']};base64,{blob}"
                        if blob
                        else resource.get("uri")
                    )

    kind = item.get("type")
    if kind == "userMessage":
        content(item.get("content"))
    elif kind == "imageView":
        add(item.get("path"))
    elif kind == "imageGeneration":
        if item.get("savedPath"):
            add(item["savedPath"])
        elif item.get("result"):
            result = item["result"]
            add(
                result
                if result.startswith("data:")
                else "data:image/png;base64," + result
            )
    elif kind == "mcpToolCall":
        content((item.get("result") or {}).get("content"))
    elif kind == "dynamicToolCall":
        content(item.get("contentItems"))
    elif kind == "functionCallOutput":
        content(item.get("output"))
    elif kind in ("agentMessage", "plan"):
        for block in MarkdownIt().parse(item.get("text") or ""):
            for token in block.children or []:
                if token.type == "image":
                    add(token.attrGet("src"), token.content)
                elif token.type == "link_open":
                    href = token.attrGet("href") or ""
                    if (
                        isinstance(href, str)
                        and Path(urlsplit(href).path).suffix.lower()
                        in IMAGE_EXTENSIONS
                    ):
                        add(href)
    # Preserve order while suppressing repeated links within one message.
    # 保持消息顺序，同一条消息的重复图片只显示一次。
    return list({source.uri: source for source in found}.values())


def load_image(source: ImageSource) -> Image.Image:
    """Decode a bounded local/data image off the UI thread.

    在后台读取本地或内嵌图片；网络引用保留为链接，避免自动访问外部资源。

    Raises:
        ValueError: Unsupported or oversized image. 不支持或超限的图片。
        OSError: Missing or unreadable file. 图片缺失或无法读取。
    """
    uri = source.uri
    if uri.startswith("data:"):
        header, separator, encoded = uri.partition(",")
        if (
            not separator
            or not header.startswith("data:image/")
            or not header.endswith(";base64")
        ):
            raise ValueError(tr("不支持的图片格式"))
        if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
            raise ValueError(tr("图片过大，无法预览"))
        data = base64.b64decode(encoded, validate=True)
    else:
        parsed = urlsplit(uri)
        # Windows drive letters are paths, not URL schemes. 兼容 Windows 盘符。
        windows_path = (
            len(parsed.scheme) == 1 and len(uri) > 2 and uri[1] == ":"
        )
        if parsed.scheme and parsed.scheme != "file" and not windows_path:
            raise ValueError(tr("此图片没有可读取的本地数据"))
        if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
            raise ValueError(tr("此图片没有可读取的本地数据"))
        path = Path(
            unquote(parsed.path) if parsed.scheme == "file" else unquote(uri)
        ).expanduser()
        if not path.is_absolute():
            path = Path(source.cwd) / path
        if not path.is_file():
            raise OSError(tr("图片文件不存在"))
        with path.open("rb") as stream:
            data = stream.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError(tr("图片过大，无法预览"))
    with Image.open(io.BytesIO(data)) as original:
        if original.width * original.height > MAX_IMAGE_PIXELS:
            raise ValueError(tr("图片过大，无法预览"))
        # EXIF orientation and first-frame thumbnails match what users see.
        # 按 EXIF 修正方向；动画只显示首帧，限制每个预览的内存占用。
        original.thumbnail((1200, 1200))
        return ImageOps.exif_transpose(original).convert("RGBA")


def original_image_path(source: ImageSource, cache: Path) -> Path:
    """Resolve an explicitly requested original, saving inline bytes if needed.

    用户明确要求打开原图时解析本地路径；内嵌数据保存为缓存文件。
    """
    if source.uri.startswith("data:"):
        load_image(source)
        data = base64.b64decode(source.uri.partition(",")[2], validate=True)
        with Image.open(io.BytesIO(data)) as original:
            extension = (original.format or "png").lower()
        cache.mkdir(parents=True, exist_ok=True)
        path = cache / f"{source.key}.{extension}"
        path.write_bytes(data)
        return path
    parsed = urlsplit(source.uri)
    windows_path = len(parsed.scheme) == 1 and source.uri[1:2] == ":"
    if parsed.scheme not in ("", "file") and not windows_path:
        raise ValueError(tr("此图片没有可读取的本地数据"))
    if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
        raise ValueError(tr("此图片没有可读取的本地数据"))
    path = Path(
        unquote(parsed.path if parsed.scheme == "file" else source.uri)
    ).expanduser()
    path = path if path.is_absolute() else Path(source.cwd) / path
    if not path.is_file():
        raise OSError(tr("图片文件不存在"))
    return path.resolve()
