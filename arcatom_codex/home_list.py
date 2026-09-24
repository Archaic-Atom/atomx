"""Compact session rows and visible section bands. 紧凑会话行与独立分区标题。"""
from pathlib import Path
import time

from rich.console import Group
from rich.padding import Padding
from rich.table import Table
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


def session_row(session: Session, width: int, palette: Palette, deleting: bool):
    """Keep one row per session, adding a preview when the terminal is wide.

    宽终端对齐标题、摘要、路径；窄终端优先保留标题与路径，始终单行。
    """
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
    updated = session.meta.get("updatedAt", 0)
    date = time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else ""
    summary = Text(tr("删除中…") if deleting else " ".join(clean(preview).split()) or date,
                   palette.muted, no_wrap=True, overflow="ellipsis")
    directory = clean(session.meta.get("cwd", ""))
    home = str(Path.home())
    if directory == home or directory.startswith(home + "/") or directory.startswith(home + "\\"):
        directory = "~" + directory[len(home):]
    wide = width >= 110
    title_width = min(36, max(22, width // 3))
    path_width = max(26, width // 3) if wide else max(10, width - title_width - 2)
    path = Text(directory, palette.muted, no_wrap=True, overflow="ellipsis")
    if path.cell_len > path_width:
        # Preserve the most useful directory suffix for deep workspaces.
        while Text("…" + directory).cell_len > path_width:
            directory = directory[1:]
        path = Text("…" + directory, palette.muted, no_wrap=True)
    table = Table.grid(padding=(0, 2), expand=True)
    table.add_column(width=title_width, no_wrap=True, overflow="ellipsis")
    table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
    if wide:
        table.add_column(width=path_width, no_wrap=True, overflow="ellipsis")
        table.add_row(title, summary, path)
        return table
    table.add_row(title, path)
    return table
