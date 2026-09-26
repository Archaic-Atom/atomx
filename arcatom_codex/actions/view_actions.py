"""Transcript and status presentation. 日志与状态显示。"""

from __future__ import annotations

import json
import time
from typing import Any

from rich.console import Group
from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import (
    Button,
    OptionList,
    Static,
)
from textual.widgets.option_list import Option

from ..access import AppActions
from ..core.state import clean, number, parent_id, timestamp_seconds
from ..i18n import tr
from ..image_widgets import MediaBlock, MediaTranscript
from ..images import message_images
from ..rendering import (
    COMPACT_ACTIVITY_TYPES,
    command_summary,
    format_elapsed,
    pretty,
)
from ..statusline import build_status
from ..widgets import (
    Composer,
    SessionList,
    TranscriptScroll,
)


def fit_activity_column(value: str, width: int) -> str:
    """Fit a column by terminal cells, including wide Chinese characters.

    按终端单元格宽度截断或补齐列，兼容中文宽字符。

    Args:
        value: Cell text. 单元格文字。
        width: Target terminal-cell width. 目标终端单元格宽度。

    Returns:
        Text occupying exactly the requested width. 固定宽度的文字。
    """
    cell = Text(value, no_wrap=True)
    cell.truncate(max(1, width), overflow="ellipsis", pad=True)
    return cell.plain


def agent_elapsed(agent: dict, meta: dict, now: float, compact: bool) -> str:
    """Format known agent time without inventing a historical start.

    仅在已知开始时间时显示代理用时。

    Args:
        agent: Parent's agent state. 父会话中的代理状态。
        meta: Child thread metadata. 子线程元数据。
        now: Current wall time. 当前墙上时间。
        compact: Whether space is narrow. 是否使用窄屏格式。

    Returns:
        Elapsed time or a missing-data dash. 用时或缺失标记。
    """
    start = agent.get("startedAt") or timestamp_seconds(meta.get("createdAt"))
    if not isinstance(start, (int, float)):
        return "—"
    status = agent.get("status") or agent.get("runtimeStatus")
    terminal = status in (
        "completed",
        "idle",
        "shutdown",
        "errored",
        "interrupted",
    )
    end = agent.get("finishedAt")
    if end is None:
        end = (
            timestamp_seconds(meta.get("updatedAt")) or now if terminal else now
        )
    seconds = max(0, int(end - start))
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    if compact:
        if days:
            return f"{days}d{hours:02d}h"
        if hours:
            return f"{hours}h{minutes:02d}m"
        return f"{minutes:02d}:{seconds:02d}"
    if days:
        return f"{days}d {hours:02d}h"
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


class ViewActions(AppActions):
    """Render workspace views from cached state. 使用缓存状态渲染工作台。"""

    def paint_status(self) -> None:
        """Follow the user's Claude status line. 复用用户的状态栏规范。"""
        if not self.workspace.is_running or self.workspace._exit:
            return
        session = (
            self.workspace.store.get(self.workspace.current or "")
            if self.workspace.current
            else None
        )
        if session is None:
            options = self.workspace.query_one("#sessions", SessionList)
            if options.highlighted is not None and options.option_count:
                tid = options.get_option_at_index(options.highlighted).id
                session = self.workspace.store.sessions.get(tid or "")
        bar = build_status(
            session,
            self.workspace.store.rate_limits,
            self.workspace.size.width - 6,
            fields=self.workspace.status_fields,
            palette=self.workspace.palette,
        )
        if not self.workspace.ready:
            bar.append(tr(" · 未连接"), style=self.workspace.palette.accent)
        elif self.workspace.store.account_errors:
            bar.append(
                tr(" · 额度更新失败"), style=self.workspace.palette.accent
            )
        self.workspace.query_one("#bottom", Static).update(bar)

    def paint_waiting(self) -> None:
        """Animate independently of transcript rendering. 独立刷新等待动画。"""
        self.workspace.query_one(
            "#transcript-scroll", TranscriptScroll
        ).sync_follow()
        widget = self.workspace.query_one("#waiting", Static)
        session = self.workspace.store.sessions.get(
            self.workspace.current or ""
        )
        busy = bool(
            session
            and self.workspace.ready
            and (session.busy_since is not None or session.active_turn)
        )
        widget.display = busy
        if not busy or session is None:
            return
        now = time.monotonic()
        since = session.busy_since if session.busy_since is not None else now
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
        frame = frames[int(now * 8) % len(frames)]
        elapsed = format_elapsed(now - since)
        widget.update(
            Text.assemble(
                (
                    f"{frame} {session.phase or tr('等待 Codex')}",
                    self.workspace.palette.accent,
                ),
                (
                    f"  {elapsed}"
                    + (tr(" · Esc 停止") if session.active_turn else ""),
                    self.workspace.palette.muted,
                ),
            )
        )

    def paint_chat(self) -> None:
        """Render cached messages and live activity for the current thread.

        渲染缓存消息及实时活动。
        """
        self.workspace.call_after_refresh(
            self.workspace.query_one(Composer).fit_height
        )
        self.workspace.update_navigation_hint()
        session = self.workspace.store.get(self.workspace.current or "")
        attachments = self.workspace.query_one("#attachments", Static)
        attachments.display = bool(session.attachments)
        attachments.update(
            tr("▧ {0} 张待发送图片 · F8 管理 · Enter 发送").format(
                len(session.attachments)
            )
        )
        model = session.meta.get("model") or tr("默认模型")
        effort = clean(session.meta.get("reasoningEffort"))
        if effort:
            model += f" · {effort}"
        title = Text(
            "✦  " + session.title[:100],
            style="bold " + self.workspace.palette.accent,
            no_wrap=True,
            overflow="ellipsis",
        )
        title.append(
            "  "
            + clean(
                f"{session.meta.get('cwd', self.workspace.cwd)}"
                f"  ·  {model}  ·  {session.status}"
            ),
            style=self.workspace.palette.muted,
        )
        self.workspace.query_one("#chat-title", Static).update(title)
        self.workspace.query_one("#chat-path", Static).update("")
        scroll = self.workspace.query_one("#transcript-scroll", VerticalScroll)
        follow = (
            self.workspace.view_preferences.get("follow_output", True)
            and not scroll.has_focus
            and not self.workspace.screen.selections
            and scroll.is_vertical_scroll_end
        )
        # Cache parsed Markdown; don't reparse the whole history for each delta.
        # 缓存未变化的 Markdown，避免每个流式片段重新解析完整历史。
        blocks = []
        media_blocks = []
        has_images = False
        signatures = []
        display_fields = (
            "type",
            "text",
            "content",
            "status",
            "command",
            "changes",
            "server",
            "tool",
            "namespace",
            "receiverThreadIds",
            "query",
            "path",
            "savedPath",
            "result",
            "output",
            "contentItems",
            "failure",
            "arguments",
            "input",
            "prompt",
        )
        display_kinds = {
            "userMessage",
            "agentMessage",
            "plan",
            "commandExecution",
            "fileChange",
            "mcpToolCall",
            "webSearch",
            "notice",
            "imageView",
            "imageGeneration",
            "functionCallOutput",
            "dynamicToolCall",
            "collabAgentToolCall",
        }
        visible_items = [
            item
            for item in list(session.items.values())[-session.visible_items :]
            if item.get("type") in display_kinds
        ]
        for index, item in enumerate(visible_items):
            next_kind = (
                visible_items[index + 1].get("type")
                if index + 1 < len(visible_items)
                else None
            )
            compact_after = next_kind in COMPACT_ACTIVITY_TYPES
            visible = {k: item[k] for k in display_fields if k in item}
            visible["compactAfter"] = compact_after
            is_activity = item.get("type") in COMPACT_ACTIVITY_TYPES
            expanded = is_activity and item[
                "id"
            ] in self.workspace.expanded_activities.get(session.id, set())
            command_arrow = None
            if is_activity:
                visible["expanded"] = expanded
                started = self.workspace.activity_arrow_started.get(
                    (session.id, item["id"])
                )
                if started is not None:
                    elapsed = time.monotonic() - started
                    if elapsed >= 0.24:
                        self.workspace.activity_arrow_started.pop(
                            (session.id, item["id"])
                        )
                    else:
                        frames = ("›", "⌄") if expanded else ("⌄", "›")
                        command_arrow = frames[min(int(elapsed / 0.12), 1)]
                visible["commandArrow"] = command_arrow
                if item.get("status") in ("inProgress", "running"):
                    visible["spinnerFrame"] = (
                        self.workspace.last_activity_spinner_frame
                    )
            signature = json.dumps(visible, ensure_ascii=False, sort_keys=True)
            cache_key = session.id + ":" + item["id"]
            cached = self.workspace.transcript_cache.get(cache_key)
            if cached is None or cached[0] != signature:
                rendered = (
                    Text(
                        clean(item.get("text", ""))
                        + ("" if compact_after else "\n")
                    )
                    if self.workspace.raw_transcript
                    and item.get("type") in ("agentMessage", "plan")
                    else pretty(
                        item,
                        self.workspace.code_theme,
                        self.workspace.palette,
                        compact_after=compact_after,
                        expanded=expanded,
                        spinner_frame=self.workspace.last_activity_spinner_frame,
                        command_arrow=command_arrow,
                    )
                )
                images = message_images(
                    item, session.meta.get("cwd") or self.workspace.cwd
                )
                cached = (signature, rendered, images)
                self.workspace.transcript_cache[cache_key] = cached
            if cached[1] is not None:
                signatures.append((item["id"], signature))
                blocks.append(cached[1])
                media_blocks.append(
                    MediaBlock(cache_key, str(id(cached[1])), cached[1])
                )
            if cached[2]:
                has_images = True
                signatures.append((item["id"] + ":images", signature))
                for image in cached[2]:
                    key = cache_key + ":image:" + image.key
                    media_blocks.append(MediaBlock(key, image.key, image))
        if not blocks:
            message = (
                tr("正在加载最近的会话记录…")
                if session.history_loading
                else session.history_error
                or tr(
                    (
                        "\n开始一段新的工作。\n\n直接描述任务；需要分工时，可以明确让 Codex"
                        " 使用子代理。"
                    )
                )
            )
            blocks = [Text(message, style=self.workspace.palette.muted)]
        older = self.workspace.query_one("#older-history", Button)
        older.display = bool(
            session.history_cursor or len(session.items) > session.visible_items
        )
        older.disabled = session.history_loading
        transcript_signature = (
            session.id,
            signatures,
            session.history_loading,
            session.history_error,
        )
        if transcript_signature != self.workspace.transcript_signature:
            self.workspace.transcript_signature = transcript_signature
            text_view = self.workspace.query_one("#transcript", Static)
            media_view = self.workspace.query_one(
                "#media-transcript", MediaTranscript
            )
            text_view.display = not has_images
            media_view.display = has_images
            media_view.update_blocks(
                media_blocks if has_images else [], follow=follow
            )
            if not has_images:
                text_view.update(Group(*blocks))
        visible_agents = session.visible_agents()
        now = time.time()
        narrow_activity = self.workspace.size.width < 70
        elapsed_by_tid = {
            tid: agent_elapsed(
                agent,
                self.workspace.store.get(tid).meta,
                now,
                narrow_activity,
            )
            for tid, agent in visible_agents.items()
        }
        turn_id = session.active_turn or session.last_turn_id
        thread_ids = (session.id, *visible_agents)
        threads = [self.workspace.store.get(tid) for tid in thread_ids]
        commands = [
            item
            for thread in threads
            for item in thread.items.values()
            if item.get("type") == "commandExecution"
            and (
                thread.id != session.id
                or session.item_turns.get(item["id"]) == turn_id
            )
        ]
        tools = [
            item
            for thread in threads
            for item in thread.items.values()
            if item.get("type") in ("dynamicToolCall", "mcpToolCall")
            and (
                thread.id != session.id
                or session.item_turns.get(item["id"]) == turn_id
            )
        ]
        running_agents = any(
            agent.get("status") in ("pendingInit", "running", "active")
            for agent in visible_agents.values()
        )
        visible_terminals = [
            terminal
            for terminal in session.terminals
            if terminal.get("agentThreadId", session.id) in thread_ids
        ]
        options = self.workspace.query_one("#activities", OptionList)
        if (
            self.workspace.activity_auto_tid
            and self.workspace.activity_auto_tid != session.id
        ):
            self.workspace.detail_open = False
            self.workspace.activity_auto_tid = None
            options.display = False
        if (
            running_agents
            and (
                session.active_turn
                or session.meta.get("status", {}).get("type") == "active"
            )
            and not self.workspace.detail_open
            and self.workspace.activity_manual_closed.get(session.id)
            != session.active_turn
        ):
            self.workspace.detail_open = True
            self.workspace.activity_auto_tid = session.id
            options.display = True
        elif (
            not running_agents
            and self.workspace.activity_auto_tid == session.id
        ):
            self.workspace.detail_open = False
            self.workspace.activity_auto_tid = None
            options.display = False
            if options.has_focus:
                self.workspace.query_one("#composer").focus()
        self.workspace.query_one("#activity-summary", Static).update(
            Text(
                tr(
                    "{0}  子代理 {1}  ·  后台进程 {2}  ·  工具 {3}  ·  命令 {4}    Ctrl+T 展开/收起"
                ).format(
                    "▾" if self.workspace.detail_open else "▸",
                    len(visible_agents),
                    len(visible_terminals),
                    len(tools),
                    len(commands),
                ),
                style=self.workspace.palette.accent,
            )
        )
        activity_signature = (
            session.id,
            turn_id,
            self.workspace.size.width,
            options.content_size.width,
            json.dumps(visible_agents, sort_keys=True),
            tuple(elapsed_by_tid.items()),
            json.dumps(visible_terminals, sort_keys=True),
            [
                (
                    thread.id,
                    thread.status,
                    thread.total,
                    [
                        (item["id"], item.get("status"), item.get("exitCode"))
                        for item in thread.items.values()
                        if item.get("type")
                        in (
                            "commandExecution",
                            "dynamicToolCall",
                            "mcpToolCall",
                        )
                    ],
                )
                for thread in threads
            ],
            session.terminal_error,
        )
        if self.workspace.activity_signature == activity_signature:
            return
        self.workspace.activity_signature = activity_signature
        old_id = (
            options.get_option_at_index(options.highlighted).id
            if options.highlighted is not None and options.option_count
            else None
        )
        old_scroll_y = options.scroll_y
        options.clear_options()
        self.workspace.activity_targets = {}

        def add(key: str, label: str, target: tuple[str, Any]) -> None:
            """Add an activity row with a stable action target. 添加有稳定目标的活动条目。"""
            options.add_option(
                Option(Text(label, no_wrap=True, overflow="ellipsis"), id=key)
            )
            self.workspace.activity_targets[key] = target

        add(
            "main-" + session.id,
            f"  ◆ Main  ·  {session.title}  ·  {session.status}",
            ("thread", session.id),
        )
        children: dict[str, list[str]] = {}
        for tid, agent in visible_agents.items():
            child = self.workspace.store.get(tid)
            parent = agent.get("parentId") or parent_id(child.meta)
            if parent not in visible_agents and parent != session.id:
                parent = session.id
            children.setdefault(parent, []).append(tid)
        visited: set[str] = set()

        def add_descendants(parent: str, depth: int) -> None:
            """Add each agent under its actual parent. 按真实父子关系显示代理。"""
            for tid in children.get(parent, []):
                if tid in visited:
                    continue
                visited.add(tid)
                agent = visible_agents[tid]
                child = self.workspace.store.get(tid)
                status = str(
                    agent.get("status")
                    or agent.get("runtimeStatus")
                    or "notLoaded"
                )
                state = {
                    "running": tr("运行中"),
                    "active": tr("运行中"),
                    "completed": tr("已完成"),
                    "idle": tr("就绪"),
                    "shutdown": tr("已关闭"),
                    "errored": tr("异常"),
                    "interrupted": tr("已中断"),
                    "pendingInit": tr("启动中"),
                    "notLoaded": tr("未加载"),
                }.get(status, status)
                name = clean(
                    agent.get("name")
                    or child.meta.get("agentNickname")
                    or (tid if len(tid) <= 24 else tid[:8])
                ).rsplit("/", 1)[-1]
                activity = clean(agent.get("activity"))
                suffix = f"  ·  {command_summary(activity)}" if activity else ""
                separator = " " if narrow_activity else " │ "
                state_width = 9 if narrow_activity else 12
                usage_width = 10 if narrow_activity else 11
                elapsed_width = 6 if narrow_activity else 8
                available = (
                    (
                        options.content_size.width
                        or self.workspace.size.width - 8
                    )
                    - 2
                    - state_width
                    - usage_width
                    - elapsed_width
                    - 3 * len(separator)
                )
                # Indent and name share one fixed column across every depth.
                # 缩进和名称共用固定列，深层代理也不会推移右侧分隔线。
                name_column_width = min(40, max(10, available))
                visual_depth = min(depth, max(1, (name_column_width - 10) // 2))
                prefix = f"  {'  ' * visual_depth}◇ "
                name_width = name_column_width - len(prefix)
                columns = separator.join(
                    (
                        prefix + fit_activity_column(name, name_width),
                        fit_activity_column(state, state_width),
                        fit_activity_column(
                            f"{number(child.total)} tokens", usage_width
                        ),
                        fit_activity_column(elapsed_by_tid[tid], elapsed_width),
                    )
                )
                add(
                    "a-" + tid,
                    f"{columns}{suffix}",
                    ("thread", tid),
                )
                add_descendants(tid, depth + 1)

        add_descendants(session.id, 1)
        for tid in visible_agents:
            if tid not in visited:
                children.setdefault(session.id, []).append(tid)
        add_descendants(session.id, 1)
        if session.terminal_error:
            options.add_option(
                Option(
                    Text(
                        tr("后台进程信息不可用：")
                        + session.terminal_error[:90],
                        style=self.workspace.palette.muted,
                    ),
                    disabled=True,
                )
            )
        matching = next(
            (
                index
                for index in range(options.option_count)
                if options.get_option_at_index(index).id == old_id
            ),
            0,
        )
        options.highlighted = matching
        options.scroll_to(y=old_scroll_y, animate=False, immediate=True)
