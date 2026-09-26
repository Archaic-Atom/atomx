"""Session creation, submission and cancellation. 会话创建、提交与停止。"""

from __future__ import annotations

import asyncio
import time
import uuid

from ..access import AppActions
from ..backend.rpc import RpcError
from ..i18n import tr
from ..personal import turn_context
from ..preferences import approval_defaults
from ..widgets import (
    Composer,
    SessionList,
    TranscriptScroll,
)


class TurnActions(AppActions):
    """Keep mutations scoped to their original thread. 将写操作限定在原始会话。"""

    async def delete_session(self, tid: str, index: int) -> None:
        """Commit deletion after backend success. 后端成功后移除本地条目。"""
        title = self.workspace.store.get(tid).title
        try:
            await self.workspace.client.call("thread/delete", {"threadId": tid})
            self.workspace.store.remove_thread(tid)
            self.workspace.paint_sessions()
            options = self.workspace.query_one("#sessions", SessionList)
            if options.has_focus and options.option_count:
                options.highlighted = min(index, options.option_count - 1)
            self.workspace.paint_status()
            self.workspace.notify(tr("已删除：") + title[:50])
        except RpcError:
            self.workspace.notify(
                tr("删除未确认，条目暂时保留；Ctrl+R 可刷新检查。"),
                severity="warning",
            )
            raise
        finally:
            self.workspace.deleting.discard(tid)
            self.workspace.paint_sessions()

    async def send_prompt(
        self, tid: str, prompt_override: str | None = None
    ) -> None:
        """Echo before awaiting RPC and retain failed drafts. 先回显，再等待后端。"""
        composer = self.workspace.query_one("#composer", Composer)
        prompt = (
            composer.text.strip()
            if prompt_override is None
            else prompt_override
        )
        images = (
            list(self.workspace.store.get(tid).attachments)
            if prompt_override is None
            else []
        )
        if self.workspace.pasting and prompt_override is None:
            self.workspace.notify(tr("图片正在粘贴，请稍后按 Enter。"))
            return
        if not prompt and not images:
            return
        if tid in self.workspace.sending:
            self.workspace.notify(tr("上一条消息仍在发送，当前输入已保留。"))
            return
        if prompt_override is None and prompt.startswith("/"):
            await self.workspace.slash(prompt)
            return
        if not self.workspace.ready:
            self.workspace.notify(
                tr("尚未连接 Codex，请按 Ctrl+R 重试"), severity="warning"
            )
            return
        if self.workspace.auth_needed:
            self.workspace.action_login()
            return
        session = self.workspace.store.get(tid)
        content = ([{"type": "text", "text": prompt}] if prompt else []) + [
            {"type": "localImage", "path": path} for path in images
        ]
        message_id = str(uuid.uuid4())
        session.pending_messages[message_id] = prompt
        session.items[message_id] = {
            "id": message_id,
            "type": "userMessage",
            "clientId": message_id,
            "content": content,
        }
        session.draft = ""
        if prompt_override is None:
            composer.clear()
            composer.reset_history()
            session.attachments.clear()
        self.workspace.sending.add(tid)
        session.busy_since = session.busy_since or time.monotonic()
        session.phase = (
            tr("正在恢复会话") if not session.resumed else tr("正在发送消息")
        )
        if self.workspace.current == tid:
            self.workspace.screen.clear_selection()
            self.workspace.focus_composer(edit=True)
            self.workspace.query_one(
                "#transcript-scroll", TranscriptScroll
            ).sync_follow(restart=True)
        self.workspace.store.revision += 1
        self.workspace.paint(force=True)
        try:
            await self.workspace.ensure_resumed(tid)
            session.phase = tr("等待 Codex")
            params: dict = {
                "threadId": tid,
                "clientUserMessageId": message_id,
                "input": content,
            }
            context = await asyncio.to_thread(
                turn_context,
                prompt,
                self.workspace.personal_skills,
                self.workspace.personal_instructions,
            )
            if context:
                params["additionalContext"] = context
            if tid in self.workspace.stop_requested and not session.active_turn:
                session.pending_messages.pop(message_id, None)
                session.items.pop(message_id, None)
                session.attachments[0:0] = images
                if self.workspace.current == tid and not composer.text:
                    composer.load_text(prompt)
                else:
                    session.draft = prompt
                session.busy_since = None
                session.phase = ""
                self.workspace.stop_requested.discard(tid)
                return
            if session.active_turn:
                params["expectedTurnId"] = session.active_turn
                await self.workspace.client.call("turn/steer", params)
            else:
                await self.workspace.client.call("turn/start", params)
        except Exception as exc:
            session.attachments[0:0] = images
            # A timeout may have been accepted: do not silently send it again.
            # 超时可能已经被后端接受，显示状态不明，绝不自动重发。
            if message_id in session.pending_messages:
                session.pending_messages.pop(message_id, None)
                session.items.pop(message_id, None)
                session.ingest(
                    {
                        "id": "send-error-" + message_id,
                        "type": "notice",
                        "text": tr(
                            "消息发送未确认，请先检查会话后再决定是否重试。\n"
                        )
                        + prompt
                        + "\n"
                        + str(exc),
                    }
                )
                if self.workspace.current == tid and not composer.text:
                    composer.load_text(prompt)
                elif self.workspace.current != tid and not session.draft:
                    session.draft = prompt
            if not session.active_turn:
                self.workspace.stop_requested.discard(tid)
                session.busy_since = None
                session.phase = ""
            raise
        finally:
            self.workspace.sending.discard(tid)
            self.workspace.store.revision += 1

    async def create_session(self, cwd: str) -> None:
        """Create a thread using the chosen directory and approval mode.

        按目录及审批模式创建会话。
        """
        if self.workspace.creating:
            return
        if self.workspace.auth_needed:
            self.workspace.action_login()
            return
        self.workspace.creating = True
        try:
            desired = bool(
                self.workspace.view_preferences.get("approve_for_me", True)
            )
            result = await self.workspace.client.call(
                "thread/start",
                {
                    "cwd": cwd,
                    **approval_defaults(self.workspace.view_preferences),
                },
            )
            session = self.workspace.store.merge(result["thread"], history=True)
            session.resumed = True
            session.awaiting_input = True
            session.approval_default_applied = desired
            self.workspace.apply_runtime(session, result)
            await self.workspace.open_session(session.id)
        finally:
            self.workspace.creating = False

    async def interrupt_turn(self, tid: str) -> None:
        """Await a known turn ID; never resend while stopping. 等待回合 ID 后停止。"""
        session = self.workspace.store.get(tid)
        if tid in self.workspace.interrupting or (
            not session.active_turn and tid in self.workspace.sending
        ):
            return
        self.workspace.interrupting.add(tid)
        try:
            if not session.active_turn:
                page = await self.workspace.client.call(
                    "thread/turns/list",
                    {
                        "threadId": tid,
                        "limit": 1,
                        "itemsView": "notLoaded",
                        "sortDirection": "desc",
                    },
                )
                session.active_turn = next(
                    (
                        turn["id"]
                        for turn in page.get("data", [])
                        if turn.get("status") == "inProgress"
                    ),
                    None,
                )
            if not session.active_turn:
                self.workspace.interrupting.discard(tid)
                self.workspace.stop_requested.discard(tid)
                return
            await self.workspace.client.call(
                "turn/interrupt",
                {"threadId": tid, "turnId": session.active_turn},
            )
        except Exception:
            self.workspace.interrupting.discard(tid)
            self.workspace.stop_requested.discard(tid)
            raise
