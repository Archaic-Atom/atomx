"""Backend subscriptions and request handling. 后端订阅与请求处理。"""

from __future__ import annotations

import asyncio
import json

from .access import AppActions
from .dialogs import Approval, Question
from .i18n import tr
from .rpc import RpcError
from .state import clean, rollout_usage


class BackendActions(AppActions):
    """Coordinate backend lifecycle and user requests. 协调后端生命周期和用户请求。"""

    async def connect(self) -> None:
        """Load the backend and start event consumers. 连接后端并启动事件读取。"""
        try:
            await self.workspace.client.start()
            self.workspace.ready = True
            self.workspace.connection_text = (
                tr("● 演示模式 · 以下为示例数据，不会调用模型")
                if self.workspace.demo
                else tr("● 已连接本机 Codex")
            )
            self.workspace.run_worker(
                self.workspace.consume(),
                group="rpc-events",
                exclusive=True,
                exit_on_error=False,
            )
            self.workspace.run_worker(
                self.workspace.approvals(),
                group="rpc-approvals",
                exclusive=True,
                exit_on_error=False,
            )
            await self.workspace.load_sessions()
            self.workspace.run_worker(
                self.workspace.load_account(), exit_on_error=False
            )
            if self.workspace.poll_timer is None:
                self.workspace.poll_timer = self.workspace.set_interval(
                    4, self.workspace.poll_current
                )
            if self.workspace.account_timer is None:
                self.workspace.account_timer = self.workspace.set_interval(
                    60, self.workspace.refresh_account
                )
        except Exception as exc:
            self.workspace.connection_text = tr("! 连接失败 · Ctrl+R 重试")
            self.workspace.notify(clean(str(exc)), severity="error", timeout=15)
        self.workspace.paint(force=True)

    async def load_sessions(self) -> None:
        """Merge visible server threads into the session store. 将服务端会话合并到列表。"""
        async for batch in self.workspace.client.pages(
            "thread/list",
            {"limit": 100, "modelProviders": [], "sortKey": "updated_at"},
        ):
            for meta in batch:
                meta["archived"] = False
                self.workspace.store.merge(meta)
            self.workspace.paint(force=True)
            # Usage snapshots are read on a worker thread; no account credentials are read.
            # 用量快照在工作线程读取，不读取账户凭证。
            usage = await asyncio.to_thread(
                lambda: {t["id"]: rollout_usage(t.get("path")) for t in batch}
            )
            for tid, value in usage.items():
                if value and not self.workspace.store.get(tid).usage:
                    self.workspace.store.get(tid).usage = value
        self.workspace.store.revision += 1

    async def load_account(self) -> None:
        """Read account and model metadata without blocking rendering.

        异步读取账户与模型信息。
        """
        if self.workspace.account_loading:
            return
        self.workspace.account_loading = True
        self.workspace.store.account_errors = []

        async def read(method: str, field: str) -> None:
            """Load an optional backend metadata field. 加载可选的后端信息字段。"""
            try:
                setattr(
                    self.workspace.store,
                    field,
                    await self.workspace.client.call(method, timeout=12),
                )
            except RpcError as exc:
                self.workspace.store.account_errors.append(f"{method}: {exc}")

        try:
            await asyncio.gather(
                read("account/usage/read", "account_usage"),
                read("account/rateLimits/read", "rate_limits"),
            )
        finally:
            self.workspace.account_loading = False
            self.workspace.store.revision += 1

    def refresh_account(self) -> None:
        """Schedule account refresh unless a request is already pending.

        避免重复刷新账户请求。
        """
        if self.workspace.ready:
            self.workspace.launch(self.workspace.load_account())

    async def consume(self) -> None:
        """Reduce incoming events and queue interactive requests.

        归并事件并排队交互请求。
        """
        while True:
            message = await self.workspace.client.events.get()
            if "id" in message:
                tid = message.get("params", {}).get("threadId")
                if tid:
                    self.workspace.store.get(tid).pending_requests.add(
                        str(message["id"])
                    )
                    self.workspace.store.revision += 1
                await self.workspace.requests.put(message)
                continue
            method, params = (
                message.get("method", ""),
                message.get("params", {}),
            )
            if method == "client/disconnected":
                self.workspace.ready = False
                self.workspace.stop_requested.clear()
                self.workspace.interrupting.clear()
                for session in self.workspace.store.sessions.values():
                    session.busy_since = None
                    session.resumed = False
                    session.active_turn = None
                    session.pending_requests.clear()
                    session.meta["status"] = {"type": "notLoaded"}
                self.workspace.connection_text = tr(
                    "! Codex 已断开 · Ctrl+R 重连"
                )
                self.workspace.notify(
                    clean(params.get("message")), severity="error"
                )
            else:
                self.workspace.store.event(method, params)
                tid = params.get("threadId")
                if (
                    method == "turn/started"
                    and tid in self.workspace.stop_requested
                ):
                    self.workspace.launch(self.workspace.interrupt_turn(tid))
                elif method == "turn/completed":
                    self.workspace.stop_requested.discard(tid)
                    self.workspace.interrupting.discard(tid)
                if self.workspace.current in self.workspace.store.removed:
                    self.workspace.current = None
                    self.workspace.show_home()
                if (
                    params.get("threadId") == self.workspace.current
                    and self.workspace.current
                ):
                    self.workspace.store.get(
                        self.workspace.current or ""
                    ).unread = False
            if (
                method == "turn/completed"
                and params.get("threadId") != self.workspace.current
            ):
                session = self.workspace.store.get(params["threadId"])
                self.workspace.notify(session.title[:50] + tr(" · 任务已结束"))

    async def approvals(self) -> None:
        """Present backend approvals and return explicit decisions.

        展示后端审批并返回明确选择。
        """
        while True:
            message = await self.workspace.requests.get()
            method, params, rid = (
                message["method"],
                message.get("params", {}),
                message["id"],
            )
            tid = params.get("threadId")
            title = self.workspace.store.get(tid).title if tid else "Codex"
            try:
                if method in (
                    "item/commandExecution/requestApproval",
                    "item/fileChange/requestApproval",
                ):
                    details = tr("会话：{0}\n\n").format(title) + json.dumps(
                        params, ensure_ascii=False, indent=2
                    )
                    if tid and params.get("itemId"):
                        item = self.workspace.store.get(tid).items.get(
                            params["itemId"], {}
                        )
                        details += "\n\n" + json.dumps(
                            item, ensure_ascii=False, indent=2
                        )
                    decisions = params.get("availableDecisions")
                    accepted = await self.workspace.push_screen_wait(
                        Approval(
                            tr("需要你的确认"),
                            details,
                            not decisions or "accept" in decisions,
                        )
                    )
                    await self.workspace.client.reply(
                        rid, {"decision": "accept" if accepted else "decline"}
                    )
                elif method == "item/permissions/requestApproval":
                    accepted = await self.workspace.push_screen_wait(
                        Approval(
                            tr("请求额外权限 · ") + title,
                            json.dumps(params, ensure_ascii=False, indent=2),
                        )
                    )
                    await self.workspace.client.reply(
                        rid,
                        {
                            "permissions": params.get("permissions", {})
                            if accepted
                            else {},
                            "scope": "turn",
                        },
                    )
                elif method == "item/tool/requestUserInput":
                    answers = {}
                    for question in params.get("questions", []):
                        answer = await self.workspace.push_screen_wait(
                            Question(question)
                        )
                        answers[question["id"]] = {
                            "answers": [] if answer is None else [answer]
                        }
                    await self.workspace.client.reply(rid, {"answers": answers})
                elif method == "mcpServer/elicitation/request":
                    await self.workspace.push_screen_wait(
                        Approval(
                            tr("此扩展表单暂不支持 · ") + title,
                            json.dumps(params, ensure_ascii=False, indent=2),
                            False,
                        )
                    )
                    await self.workspace.client.reply(
                        rid, {"action": "decline", "content": None}
                    )
                else:
                    await self.workspace.client.reject_unsupported(rid)
                    self.workspace.notify(
                        tr("暂不支持的交互已拒绝：") + method,
                        severity="warning",
                    )
            except RpcError as exc:
                self.workspace.notify(str(exc), severity="error")
            finally:
                if tid:
                    self.workspace.store.get(tid).pending_requests.discard(
                        str(rid)
                    )
                    self.workspace.store.revision += 1
