"""Home Actions. 工作台分区行为。"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widgets import (
    ContentSwitcher,
    Input,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option, OptionDoesNotExist

from ..access import AppActions
from ..core.state import number
from ..home_list import section_heading, session_row
from ..i18n import tr
from ..widgets import Composer, SessionList


class HomeActions(AppActions):
    """Workspace home actions. 工作台对应分区操作。"""

    def redraw_directory(self, tid: str | None, step: int) -> None:
        """Update one marquee row without changing selection. 更新路径滚动且保持选择。"""
        options = self.workspace.query_one("#sessions", OptionList)
        if (
            self.workspace.current is not None
            or not options.is_on_screen
            or options.size.width < 7
        ):
            return
        if tid and tid in self.workspace.store.sessions:
            try:
                option = options.get_option(tid)
            except OptionDoesNotExist:
                return
            row = session_row(
                self.workspace.store.get(tid),
                max(7, options.size.width - 3),
                self.workspace.palette,
                tid in self.workspace.deleting,
                step,
            )
            if (
                not isinstance(option.prompt, Text)
                or option.prompt.plain != row.plain
            ):
                options.replace_option_prompt(tid, row)

    def advance_directory(self) -> None:
        """Animate only the selected row without changing focus. 仅滚动选中目录。"""
        if (
            not self.workspace.is_running
            or self.workspace._exit
            or self.workspace.current
            or self.workspace.screen is not self.workspace.main_screen
        ):
            return
        options = next(iter(self.workspace.query(SessionList)), None)
        if (
            options is not None
            and options.has_focus
            and self.workspace.marquee_tid
        ):
            self.workspace.marquee_step += 1
            self.workspace.redraw_directory(
                self.workspace.marquee_tid, self.workspace.marquee_step
            )

    def action_delete_session(self) -> None:
        """Require two presses on the same history. 同一会话连按两次才删除。"""
        if (
            not self.workspace.ready
            or self.workspace.current is not None
            or self.workspace.screen is not self.workspace.main_screen
        ):
            return
        options = self.workspace.query_one("#sessions", OptionList)
        if (
            not options.has_focus
            or options.highlighted is None
            or not options.option_count
        ):
            return
        tid = options.get_option_at_index(options.highlighted).id
        if tid not in self.workspace.store.sessions:
            return
        if tid in self.workspace.deleting:
            return
        session = self.workspace.store.get(tid)
        if (
            session.active_turn
            or tid in self.workspace.sending
            or session.pending_requests
            or session.meta.get("status", {}).get("type") == "active"
        ):
            self.workspace.delete_confirmation = None
            self.workspace.notify(
                tr("这个会话还在运行，请先进入会话停止任务。"),
                severity="warning",
            )
            return
        now = time.monotonic()
        if (
            not self.workspace.delete_confirmation
            or self.workspace.delete_confirmation[0] != tid
            or now > self.workspace.delete_confirmation[1]
        ):
            self.workspace.delete_confirmation = (tid, now + 3)
            self.workspace.notify(
                tr(
                    "3 秒内再按一次 Ctrl+X，永久删除「{0}」；其他操作取消。"
                ).format(session.title[:50]),
                severity="warning",
                timeout=3,
            )
            return
        self.workspace.delete_confirmation = None
        index = options.highlighted
        self.workspace.deleting.add(tid)
        self.workspace.paint_sessions()
        self.workspace.launch(self.workspace.delete_session(tid, index))

    def paint_sessions(self) -> None:
        """Rebuild grouped rows while preserving the selected thread.

        重建分组并保持所选会话。
        """
        if not self.workspace.is_running or self.workspace._exit:
            return
        sessions = self.workspace.store.roots(
            self.workspace.query_one("#search", Input).value
        )
        options = self.workspace.query_one("#sessions", OptionList)
        selected = None
        if options.highlighted is not None and options.option_count:
            selected = options.get_option_at_index(options.highlighted).id
        options.clear_options()
        width = max(7, options.size.width - 3)
        for section, label, color in (
            (
                "waiting",
                tr("等待你确认 / 输入"),
                self.workspace.palette.warning,
            ),
            ("working", tr("正在工作"), self.workspace.palette.success),
            ("history", tr("历史会话"), self.workspace.palette.muted),
        ):
            members = [s for s in sessions if s.section == section]
            options.add_option(
                Option(
                    section_heading(
                        label,
                        len(members),
                        color,
                        self.workspace.palette,
                        section == "waiting",
                    ),
                    disabled=True,
                )
            )
            for session in members:
                options.add_option(
                    Option(
                        session_row(
                            session,
                            width,
                            self.workspace.palette,
                            session.id in self.workspace.deleting,
                            self.workspace.marquee_step
                            if session.id == selected
                            else 0,
                        ),
                        id=session.id,
                    )
                )
        if not options.has_focus:
            options.highlighted = None
        elif selected:
            for i in range(options.option_count):
                if options.get_option_at_index(i).id == selected:
                    options.highlighted = i
                    break
        if options.has_focus and (
            options.highlighted is None
            or options.get_option_at_index(options.highlighted).disabled
        ):
            options.highlighted = next(
                (
                    i
                    for i in range(options.option_count)
                    if not options.get_option_at_index(i).disabled
                ),
                None,
            )
        lifetime = self.workspace.store.account_usage.get("summary", {}).get(
            "lifetimeTokens"
        )
        active = sum(
            s.section == "working" for s in self.workspace.store.roots()
        )
        self.workspace.query_one("#overview", Static).update(
            Text(
                tr("{0} 个会话    {1} 个运行中    账户累计 {2} tokens").format(
                    len(self.workspace.store.roots()), active, number(lifetime)
                ),
                style=self.workspace.palette.accent,
            )
        )

    def move_home_focus(self, direction: int) -> None:
        """Keep arrows within sessions. 方向键只选择会话，不经过按钮和输入框。"""
        options = self.workspace.query_one("#sessions", SessionList)
        indices = options.selectable_indices()
        if not indices:
            self.workspace.query_one("#search").focus()
            return
        self.workspace.screen.set_focus(options)
        options.highlighted = indices[0 if direction > 0 else -1]

    def show_home(self) -> None:
        """Save the current draft and restore the home screen. 保存草稿并返回首页。"""
        if self.workspace.current:
            self.workspace.home_selection = self.workspace.current
            self.workspace.store.get(
                self.workspace.current
            ).draft = self.workspace.query_one("#composer", Composer).text
        self.workspace.query_one("#view", ContentSwitcher).current = "home"
        self.workspace.current = None
        self.workspace.hide_commands()
        self.workspace.paint(force=True)
        options = self.workspace.query_one("#sessions", SessionList)
        if options.selectable_indices():
            self.workspace.screen.set_focus(options)
            for index in options.selectable_indices():
                if (
                    options.get_option_at_index(index).id
                    == self.workspace.home_selection
                ):
                    options.highlighted = index
                    break
        else:
            self.workspace.query_one("#search").focus()
