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

from .access import AppActions
from .i18n import tr
from .image_widgets import MediaBlock, MediaTranscript
from .images import message_images
from .rendering import command_summary, pretty
from .state import clean, number
from .statusline import build_status
from .widgets import (
    Composer,
    SessionList,
)


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
        elapsed = max(0, int(now - since))
        widget.update(
            Text.assemble(
                (
                    f"{frame} {session.phase or tr('等待 Codex')}",
                    self.workspace.palette.accent,
                ),
                (
                    f"  {elapsed}s"
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
        self.workspace.query_one("#chat-title", Static).update(
            Text(
                "✦  " + session.title[:100],
                style="bold " + self.workspace.palette.accent,
            )
        )
        model = session.meta.get("model") or tr("默认模型")
        self.workspace.query_one("#chat-path", Static).update(
            clean(
                f"{session.meta.get('cwd', self.workspace.cwd)}  ·  {model}  ·  {session.status}"
            )
        )
        scroll = self.workspace.query_one("#transcript-scroll", VerticalScroll)
        follow = (
            self.workspace.view_preferences.get("follow_output", True)
            and not scroll.has_focus
            and scroll.is_vertical_scroll_end
        )
        # Cache parsed Markdown; don't reparse the whole history for each delta.
        # 缓存未变化的 Markdown，避免每个流式片段重新解析完整历史。
        blocks = []
        media_blocks = []
        has_images = False
        signatures = []
        for item in list(session.items.values())[-session.visible_items :]:
            display_fields = (
                "type",
                "text",
                "content",
                "status",
                "command",
                "changes",
                "server",
                "tool",
                "query",
                "path",
                "savedPath",
                "result",
                "output",
                "contentItems",
                "failure",
            )
            if item.get("type") not in {
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
            }:
                continue
            visible = {k: item[k] for k in display_fields if k in item}
            signature = json.dumps(visible, ensure_ascii=False, sort_keys=True)
            cache_key = session.id + ":" + item["id"]
            cached = self.workspace.transcript_cache.get(cache_key)
            if cached is None or cached[0] != signature:
                rendered = (
                    Text(clean(item.get("text", "")) + "\n")
                    if self.workspace.raw_transcript
                    and item.get("type") in ("agentMessage", "plan")
                    else pretty(
                        item, self.workspace.code_theme, self.workspace.palette
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
            if follow:
                scroll.scroll_end(animate=False, immediate=True)
        commands = [
            v
            for v in session.items.values()
            if v.get("type") == "commandExecution"
        ]
        self.workspace.query_one("#activity-summary", Static).update(
            Text(
                tr(
                    "{0}  子代理 {1}  ·  后台进程 {2}  ·  命令 {3}    Ctrl+T 展开/收起"
                ).format(
                    "▾" if self.workspace.detail_open else "▸",
                    len(session.agents),
                    len(session.terminals),
                    len(commands),
                ),
                style=self.workspace.palette.accent,
            )
        )
        activity_signature = (
            session.id,
            json.dumps(session.agents, sort_keys=True),
            json.dumps(session.terminals, sort_keys=True),
            [
                (c["id"], c.get("status"), c.get("exitCode"))
                for c in commands[-30:]
            ],
            [
                (tid, self.workspace.store.get(tid).total)
                for tid in session.agents
            ],
            session.terminal_error,
        )
        if self.workspace.activity_signature == activity_signature:
            return
        self.workspace.activity_signature = activity_signature
        options = self.workspace.query_one("#activities", OptionList)
        old = options.highlighted
        options.clear_options()
        self.workspace.activity_targets = {}

        def add(key: str, label: str, target: tuple[str, Any]) -> None:
            """Add an activity row with a stable action target. 添加有稳定目标的活动条目。"""
            options.add_option(
                Option(Text(label, no_wrap=True, overflow="ellipsis"), id=key)
            )
            self.workspace.activity_targets[key] = target

        for tid, agent in session.agents.items():
            status = (
                agent.get("status") or agent.get("runtimeStatus") or "unknown"
            )
            state = {
                "running": tr("运行中"),
                "active": tr("运行中"),
                "completed": tr("已完成"),
                "idle": tr("就绪"),
                "shutdown": tr("已关闭"),
                "errored": tr("异常"),
                "pendingInit": tr("启动中"),
                "notLoaded": tr("未加载"),
            }.get(status, status)
            child = self.workspace.store.get(tid)
            label = clean(
                agent.get("name") or child.meta.get("agentNickname") or tid[:8]
            )
            add(
                "a-" + tid,
                f"  ◇ {label}  ·  {state}  ·  {number(child.total)} tokens",
                ("agent", tid),
            )
        live_items = set()
        for terminal in session.terminals:
            key = terminal.get("itemId", terminal["processId"])
            live_items.add(key)
            add(
                "p-" + terminal["processId"],
                f"  ● {command_summary(terminal['command'])}"
                f"  ·  PID {terminal.get('osPid') or terminal['processId']}",
                ("process", terminal),
            )
        for item in commands[-30:]:
            if item["id"] not in live_items:
                status = item.get("status", "unknown")
                suffix = (
                    tr("退出码 {0}").format(item["exitCode"])
                    if item.get("exitCode") is not None
                    else status
                )
                add(
                    "c-" + item["id"],
                    f"  {'●' if status == 'inProgress' else '✓' if status == 'completed' else '!'}"
                    f" {command_summary(item.get('command'))}  ·  {suffix}",
                    ("command", item["id"]),
                )
        if not options.option_count:
            options.add_option(
                Option(
                    Text(
                        tr("当前没有子代理或命令"),
                        style=self.workspace.palette.muted,
                    ),
                    disabled=True,
                )
            )
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
        if old is not None and old < options.option_count:
            options.highlighted = old
        elif self.workspace.activity_targets:
            options.highlighted = 0
