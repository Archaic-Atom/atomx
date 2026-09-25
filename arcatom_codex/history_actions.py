"""History Actions. 工作台分区行为。"""

from __future__ import annotations

import asyncio

from textual.containers import VerticalScroll
from textual.widgets import (
    ContentSwitcher,
)

from .access import AppActions
from .i18n import tr
from .rpc import RpcError
from .state import Session
from .widgets import Composer


class HistoryActions(AppActions):
    """Workspace history actions. 工作台对应分区操作。"""

    async def read_history(self, tid: str) -> Session:
        """Read full history for commands that need every item.

        为需要完整记录的命令读取历史。
        """
        result = await self.workspace.client.call(
            "thread/read", {"threadId": tid, "includeTurns": True}
        )
        meta = result["thread"]
        if meta.get("historyMode") == "paginated":
            turns = []
            async for page in self.workspace.client.pages(
                "thread/turns/list",
                {
                    "threadId": tid,
                    "limit": 100,
                    "itemsView": "full",
                    "sortDirection": "asc",
                },
            ):
                turns.extend(page)
            meta["turns"] = turns
        return self.workspace.store.merge(meta, history=True)

    async def open_session(self, tid: str) -> None:
        """Switch immediately; load and subscribe in the background. 先显示再加载。"""
        if (
            tid in self.workspace.deleting
            or tid in self.workspace.store.removed
        ):
            return
        if self.workspace.current:
            self.workspace.store.get(
                self.workspace.current
            ).draft = self.workspace.query_one(Composer).text
        session = self.workspace.store.get(tid)
        self.workspace.current = tid
        session.unread = False
        self.workspace.query_one("#view", ContentSwitcher).current = "chat"
        self.workspace.query_one(Composer).load_text(session.draft)
        self.workspace.query_one(Composer).reset_history()
        self.workspace.focus_composer(edit=True)
        if (
            not session.hydrated or not session.resumed
        ) and not session.history_loading:
            session.history_loading = True
            self.workspace.launch(self.workspace.load_recent_history(tid))
        self.workspace.paint(force=True)
        self.workspace.query_one(
            "#transcript-scroll", VerticalScroll
        ).scroll_end(animate=False)
        self.workspace.launch(self.workspace.refresh_activity(tid))

    async def load_recent_history(self, tid: str, older: bool = False) -> None:
        """Page stored items while sharing live events. 分页历史与实时事件共同更新。"""
        session = self.workspace.store.get(tid)
        existing_ids = set(session.items)
        try:
            lock = self.workspace.resume_locks.setdefault(tid, asyncio.Lock())
            async with lock:
                if not session.resumed:
                    result = await self.workspace.client.call(
                        "thread/resume",
                        {
                            "threadId": tid,
                            "excludeTurns": True,
                            "initialTurnsPage": {
                                "limit": 1,
                                "itemsView": "notLoaded",
                                "sortDirection": "desc",
                            },
                        },
                    )
                    self.workspace.store.merge(result["thread"])
                    self.workspace.apply_runtime(session, result)
                    session.resumed = True
                    for turn in (result.get("initialTurnsPage") or {}).get(
                        "data", []
                    ):
                        if turn.get("status") == "inProgress":
                            session.active_turn = turn["id"]
                    if tid in self.workspace.stop_requested:
                        self.workspace.launch(
                            self.workspace.interrupt_turn(tid)
                        )
            page = await self.workspace.client.call(
                "thread/items/list",
                {
                    "threadId": tid,
                    "limit": 40,
                    "sortDirection": "desc",
                    "cursor": session.history_cursor if older else None,
                },
            )
            ordered = {}
            for entry in reversed(page.get("data", [])):
                item = entry["item"]
                current = session.items.get(item["id"])
                if current:
                    # Keep newer streamed text when a stored snapshot overlaps it.
                    # 历史快照与实时文字重叠时保留更完整的实时内容。
                    previous, incoming = (
                        current.get("text", ""),
                        item.get("text", ""),
                    )
                    if previous.startswith(incoming) and len(previous) > len(
                        incoming
                    ):
                        item = {**item, "text": previous}
                session.ingest(item)
                ordered[item["id"]] = session.items[item["id"]]
            if not older and session.hydrated:
                # Reconnect reloads a fresh tail; do not interleave cached old pages.
                # 重连后重新分页，保留加载期间的新事件及尚未确认的输入。
                live = {
                    key: value
                    for key, value in session.items.items()
                    if key not in existing_ids
                    or key in session.pending_messages
                }
                session.items = {**ordered, **live}
                session.visible_items = 40
            else:
                session.items = {**ordered, **session.items}
            session.history_cursor = page.get("nextCursor")
            session.hydrated = True
            session.history_error = ""
            if older:
                session.visible_items += 40
        except RpcError as exc:
            session.history_error = tr("会话记录加载失败：") + str(exc)
            self.workspace.notify(session.history_error, severity="error")
        finally:
            session.history_loading = False
            self.workspace.store.revision += 1
            if self.workspace.current == tid:
                self.workspace.paint(force=True)
                if (
                    not older
                    and not self.workspace.query_one(
                        "#transcript-scroll"
                    ).has_focus
                ):
                    self.workspace.query_one("#transcript-scroll").scroll_end(
                        animate=False
                    )

    def load_older_history(self) -> None:
        """Reveal cached history or request another stored page. 展示缓存或加载更早一页。"""
        if not self.workspace.current:
            return
        session = self.workspace.store.get(self.workspace.current)
        if session.history_loading:
            return
        if len(session.items) > session.visible_items:
            session.visible_items += 40
            self.workspace.paint(force=True)
        elif session.history_cursor:
            session.history_loading = True
            self.workspace.launch(
                self.workspace.load_recent_history(session.id, older=True)
            )
