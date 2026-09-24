"""Aligned session columns and path scrolling. 对齐会话列与目录滚动。"""
from pathlib import Path
import time

from rich.cells import cell_len
from rich.console import Group
from rich.padding import Padding
from rich.text import Text

from .appearance import Palette
from .i18n import tr
from .state import Session, clean


def section_heading(label: str, count: int, color: str, palette: Palette,
                    first: bool):
    """Separate groups visually without making headings selectable. 分区不可选中。"""
    heading = Text.assemble((" " + label + "  ", "bold " + color),
                            (str(count), palette.muted))
    band = Padding(heading, (0, 0), style="on " + palette.surface)
    return band if first else Group(Text(""), band)


def column_widths(width: int) -> tuple[int, int, int, int]:
    """Allocate fixed proportions, including CJK cells. 四列使用统一宽度比例。"""
    available = max(4, width - 3)
    edges = [available * percent // 100 for percent in (0, 26, 56, 82, 100)]
    return tuple(edges[i + 1] - edges[i] for i in range(4))


def columns(values: list[Text], width: int) -> Text:
    """Pad each cell so content never shifts other columns. 内容不改变列位置。"""
    row = Text(no_wrap=True, overflow="crop")
    for index, (value, size) in enumerate(zip(values, column_widths(width))):
        if index:
            row.append(" ")
        value = value.copy()
        value.truncate(size, overflow="ellipsis", pad=True)
        row.append_text(value)
    return row


def session_header(width: int, palette: Palette) -> Text:
    """Use the same geometry for labels and data. 表头与数据共用列宽。"""
    return columns([Text(tr(label), palette.muted) for label in
                    ("标题", "摘要", "目录", "最后处理")], width)


def directory_window(directory: str, width: int, step: int) -> str:
    """Scroll from start to end with a pause, without splitting wide glyphs.

    逐字滚动目录，首尾停顿；中文字符不拆成半个终端单元。

    Args:
        directory: Display path. 要显示的目录。
        width: Available terminal cells. 可用终端列数。
        step: Animation tick for the selected row. 选中条目的动画步数。

    Returns:
        The visible path segment. 当前可见的目录片段。
    """
    if cell_len(directory) <= width:
        return directory
    tail, used = len(directory), 0
    while tail and used + cell_len(directory[tail - 1]) <= width:
        tail -= 1
        used += cell_len(directory[tail])
    offset = min(tail, max(0, step % (tail + 7) - 3))
    text = Text(directory[offset:])
    text.truncate(width, overflow="crop")
    return text.plain


def session_row(session: Session, width: int, palette: Palette, deleting: bool,
                path_step: int = 0) -> Text:
    """Show title, summary, directory and last activity. 固定四列展示会话。"""
    color = {"waiting": palette.warning, "working": palette.success,
             "history": palette.muted}[session.section]
    marker = "*" if session.section == "waiting" else "●" if session.section == "working" else "·"
    title = Text(marker + " ", color)
    title.append(session.title, "bold " + palette.foreground if session.unread else palette.foreground)
    preview = next((item.get("text", "") for item in reversed(list(session.items.values()))
                    if item.get("type") == "agentMessage" and item.get("text")), "")
    if not preview:
        preview = session.phase or session.meta.get("preview", "")
    if preview == session.title:
        preview = ""
    updated = session.meta.get("updatedAt")
    date = time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else "—"
    summary = Text(tr("删除中…") if deleting else " ".join(clean(preview).split()), palette.muted)
    directory = clean(session.meta.get("cwd", "")).replace("\n", " ").expandtabs(4)
    home = str(Path.home())
    if directory == home or directory.startswith(home + "/") or directory.startswith(home + "\\"):
        directory = "~" + directory[len(home):]
    path = Text(directory_window(directory, column_widths(width)[2], path_step), palette.muted)
    return columns([title, summary, path, Text(date, palette.muted)], width)
