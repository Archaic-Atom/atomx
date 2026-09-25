"""Thread-scoped event reduction independent of rendering. 独立于渲染的会话事件归并。"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .i18n import tr


def clean(value: Any) -> str:
    """Remove terminal escapes from untrusted display text. 移除显示文本中的终端控制符。"""
    text = str(value or "")
    # Do not let tool output embed terminal escapes or control sequences.
    # 移除工具输出中的转义序列和终端控制字符。
    text = re.sub(r"\x1b\][^\x07]*(?:\x07|\x1b\\)", "", text)
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    return "".join(
        c for c in text if c in "\n\t" or ord(c) >= 32 and ord(c) != 127
    )


def number(value: int | None) -> str:
    """Format an optional usage count compactly. 紧凑显示可为空的用量数值。"""
    if value is None:
        return "—"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}m"
    if value >= 1000:
        return f"{value / 1000:.1f}k"
    return str(value)


def parent_id(meta: dict) -> str | None:
    """Resolve a parent from supported thread metadata. 从会话元数据解析父代理。"""
    source = meta.get("source")
    sub = source.get("subAgent", {}) if isinstance(source, dict) else {}
    spawn = sub.get("thread_spawn", {}) if isinstance(sub, dict) else {}
    return meta.get("parentThreadId") or spawn.get("parent_thread_id")


@dataclass
class Session:
    """Thread-local history, usage and pending UI state. 会话历史、用量与待处理界面状态。"""

    id: str
    meta: dict = field(default_factory=dict)
    items: dict[str, dict] = field(default_factory=dict)
    usage: dict = field(default_factory=dict)
    agents: dict[str, dict] = field(default_factory=dict)
    terminals: list[dict] = field(default_factory=list)
    terminal_error: str | None = None
    draft: str = ""
    active_turn: str | None = None
    last_turn_status: str | None = None
    hydrated: bool = False
    history_loading: bool = False
    history_cursor: str | None = None
    history_error: str = ""
    visible_items: int = 40
    resumed: bool = False
    unread: bool = False
    busy_since: float | None = None
    phase: str = ""
    pending_messages: dict[str, str] = field(default_factory=dict)
    streaming_items: set[str] = field(default_factory=set)
    pending_requests: set[str] = field(default_factory=set)
    awaiting_input: bool = False
    attachments: list[str] = field(default_factory=list)
    approval_default_applied: bool | None = None

    @property
    def section(self) -> str:
        """Classify live work without turning old idle threads into waiting work.

        优先使用真实审批/提问标志；历史 idle 会话仍归入历史。
        """
        state = self.meta.get("status", {})
        flags = (
            state.get("activeFlags", [])
            if state.get("type") == "active"
            else []
        )
        if self.pending_requests or any(
            f in flags for f in ("waitingOnApproval", "waitingOnUserInput")
        ):
            return "waiting"
        if (
            self.active_turn
            or self.busy_since is not None
            or state.get("type") == "active"
        ):
            return "working"
        return (
            "waiting"
            if self.awaiting_input or self.draft or self.attachments
            else "history"
        )

    @property
    def title(self) -> str:
        """Return a single-line display title. 返回单行会话标题。"""
        return clean(
            self.meta.get("name")
            or self.meta.get("agentNickname")
            or self.meta.get("preview")
            or tr("新会话")
        ).replace("\n", " ")

    @property
    def total(self) -> int | None:
        """Return cumulative token usage when available. 返回已有的累计用量。"""
        return self.usage.get("total", {}).get("totalTokens")

    @property
    def status(self) -> str:
        """Describe the current session state in the UI language. 返回本地化会话状态。"""
        if self.section == "waiting":
            return tr("等待确认 / 输入")
        state = self.meta.get("status", {})
        if self.active_turn or state.get("type") == "active":
            return tr("运行中")
        return {
            "systemError": tr("异常"),
            "idle": tr("就绪"),
            "notLoaded": tr("历史"),
        }.get(state.get("type"), tr("就绪"))

    def ingest(self, item: dict) -> None:
        """Merge an item and reconcile locally pending prompts.

        合并条目并消除待确认输入重复。
        """
        item_id = item.get("id")
        if not item_id:
            return
        if item.get("type") == "userMessage":
            client_id = item.get("clientId")
            text = "\n".join(
                c.get("text", "")
                for c in item.get("content", [])
                if c.get("type") == "text"
            )
            for pending_id, pending_text in list(self.pending_messages.items()):
                if client_id == pending_id or (
                    not client_id and text == pending_text
                ):
                    self.items.pop(pending_id, None)
                    self.pending_messages.pop(pending_id, None)
                    break
        self.items[item_id] = dict(self.items.get(item_id, {}), **item)
        if item.get("type") == "collabAgentToolCall":
            states = item.get("agentsStates", {})
            for tid in set(item.get("receiverThreadIds", [])) | set(states):
                agent = self.agents.setdefault(tid, {"id": tid})
                agent.update(states.get(tid, {}))
                if item.get("tool") in ("spawnAgent", "spawn_agent"):
                    agent["prompt"] = item.get("prompt")
                agent.setdefault("status", "pendingInit")
        if item.get("type") == "subAgentActivity":
            tid = item.get("agentThreadId")
            if tid:
                self.agents.setdefault(tid, {"id": tid}).update(
                    {"name": item.get("agentPath")}
                )


class Store:
    """Reduce backend events into isolated session state. 将后端事件归并到独立会话状态。"""

    def __init__(self) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        self.sessions: dict[str, Session] = {}
        self.account_usage: dict = {}
        self.rate_limits: dict = {}
        self.account_errors: list[str] = []
        self.revision = 0
        self.removed: set[str] = set()

    def remove_thread(self, tid: str) -> set[str]:
        """Forget a deleted thread tree. 删除线程树并忽略迟到的事件。"""
        removed = {tid}
        while True:
            descendants = {
                session.id
                for session in self.sessions.values()
                if parent_id(session.meta) in removed
            }
            for parent in removed.copy():
                if parent in self.sessions:
                    descendants.update(self.sessions[parent].agents)
            if descendants <= removed:
                break
            removed.update(descendants)
        self.removed.update(removed)
        for removed_id in removed:
            self.sessions.pop(removed_id, None)
        for session in self.sessions.values():
            for removed_id in removed:
                session.agents.pop(removed_id, None)
        self.revision += 1
        return removed

    def get(self, tid: str) -> Session:
        """Return or initialize a thread-scoped session. 获取或初始化指定会话。"""
        if tid in self.removed:
            return Session(tid)
        return self.sessions.setdefault(tid, Session(tid))

    def merge(self, meta: dict, history: bool = False) -> Session:
        """Merge thread metadata and optional stored turns. 合并会话元数据与可选历史回合。"""
        if meta["id"] in self.removed or parent_id(meta) in self.removed:
            self.removed.add(meta["id"])
            return Session(meta["id"], meta=meta)
        session = self.get(meta["id"])
        previous_updated = session.meta.get("updatedAt", 0) or 0
        session.meta.update({k: v for k, v in meta.items() if k != "turns"})
        # List snapshots may lag live work events. 列表快照不能回退实时处理时间。
        if previous_updated:
            session.meta["updatedAt"] = max(
                previous_updated, session.meta.get("updatedAt", 0) or 0
            )
        for turn in meta.get("turns", []):
            for item in turn.get("items", []):
                session.ingest(item)
            if turn.get("status") == "inProgress":
                session.active_turn = turn["id"]
        if history:
            session.hydrated = True
        pid = parent_id(meta)
        if pid:
            agent = self.get(pid).agents.setdefault(
                session.id, {"id": session.id}
            )
            agent["name"] = (
                meta.get("agentNickname")
                or meta.get("agentRole")
                or session.title
            )
            runtime = meta.get("status", {}).get("type")
            agent["runtimeStatus"] = runtime
            if runtime == "active" or (
                runtime == "idle"
                and agent.get("status")
                in (None, "running", "active", "pendingInit")
            ):
                agent["status"] = runtime
        self.revision += 1
        return session

    def event(self, method: str, params: dict) -> None:
        """Reduce one backend notification into thread-local state.

        将后端通知归并到对应会话。
        """
        if method == "thread/started":
            self.merge(params["thread"])
            return
        if method == "account/rateLimits/updated":
            self.rate_limits = params
            self.revision += 1
            return
        tid = params.get("threadId")
        if not tid:
            return
        if method == "thread/deleted":
            self.remove_thread(tid)
            return
        if tid in self.removed:
            return
        session = self.get(tid)
        if method == "thread/tokenUsage/updated":
            session.usage = params["tokenUsage"]
        elif method == "thread/name/updated":
            session.meta["name"] = params.get("threadName") or params.get(
                "name"
            )
        elif method == "thread/closed":
            session.resumed = False
        elif method == "thread/archived":
            session.meta["archived"] = True
        elif method == "thread/status/changed":
            session.meta["status"] = params["status"]
        elif method == "turn/started":
            session.meta["updatedAt"] = int(time.time())
            session.awaiting_input = False
            session.last_turn_status = None
            session.active_turn = params["turn"]["id"]
            session.meta["status"] = {"type": "active"}
            session.busy_since = session.busy_since or time.monotonic()
            session.phase = tr("Codex 正在思考")
        elif method == "turn/completed":
            turn = params["turn"]
            session.last_turn_status = (
                "failed"
                if turn.get("error")
                else turn.get("status", "completed")
            )
            session.meta["updatedAt"] = int(time.time())
            session.awaiting_input = True
            session.active_turn = None
            session.busy_since = None
            session.phase = ""
            session.streaming_items.clear()
            session.meta["status"] = {"type": "idle"}
            session.unread = True
            error = params["turn"].get("error")
            if error:
                session.ingest(
                    {
                        "id": "error-" + params["turn"]["id"],
                        "type": "notice",
                        "text": error.get("message", str(error)),
                    }
                )
        elif method in ("item/started", "item/completed"):
            session.ingest(params["item"])
            if method == "item/started":
                session.streaming_items.add(params["item"]["id"])
            else:
                session.streaming_items.discard(params["item"]["id"])
            if session.active_turn:
                item_type = params["item"].get("type")
                session.phase = (
                    {
                        "commandExecution": tr("Codex 正在执行命令"),
                        "mcpToolCall": tr("Codex 正在调用工具"),
                        "collabAgentToolCall": tr("Codex 正在协调子代理"),
                        "agentMessage": tr("Codex 正在回复"),
                    }.get(item_type, tr("Codex 正在思考"))
                    if method == "item/started"
                    else tr("等待 Codex")
                )
        elif method in (
            "item/agentMessage/delta",
            "item/plan/delta",
            "item/commandExecution/outputDelta",
        ):
            item_id = params["itemId"]
            command = method == "item/commandExecution/outputDelta"
            item = session.items.setdefault(
                item_id,
                {
                    "id": item_id,
                    "type": "commandExecution" if command else "agentMessage",
                },
            )
            key = "aggregatedOutput" if command else "text"
            item[key] = (item.get(key) or "") + params.get("delta", "")
            if not command:
                session.streaming_items.add(item_id)
                session.phase = tr("Codex 正在回复")
        elif method == "error":
            message = params.get("error", {}).get("message", tr("未知错误"))
            session.ingest(
                {
                    "id": f"error-{self.revision}",
                    "type": "notice",
                    "text": message,
                }
            )
        if method in (
            "thread/status/changed",
            "turn/started",
            "turn/completed",
            "thread/closed",
        ):
            for parent in self.sessions.values():
                agent = parent.agents.get(tid)
                if agent is not None:
                    if method == "turn/started":
                        agent["status"] = "running"
                    elif method == "turn/completed":
                        agent["status"] = (
                            "errored"
                            if params["turn"].get("error")
                            else "completed"
                        )
                    elif method == "thread/closed":
                        agent["status"] = "shutdown"
                    else:
                        agent["status"] = params["status"]["type"]
        self.revision += 1

    def roots(self, query: str = "") -> list[Session]:
        """Return visible root sessions in activity order. 按活动时间返回可见根会话。"""
        q = query.casefold()
        return sorted(
            (
                s
                for s in self.sessions.values()
                if s.meta
                and not s.meta.get("archived")
                and s.id not in self.removed
                and not parent_id(s.meta)
                and q in (s.title + " " + s.meta.get("cwd", "")).casefold()
            ),
            key=lambda s: s.meta.get("updatedAt", 0),
            reverse=True,
        )


def rollout_usage(path: str | None, codex_home: Path | None = None) -> dict:
    """Read the latest cumulative token snapshot, never sum repeated snapshots.

    This is a fallback for historical local threads, not a billing total. Read only
    Codex JSONL files and at most the last 4 MiB, keeping large histories responsive.

        读取最近累计快照，不重复累加；仅作为旧版本兼容后备。
    """
    if not path:
        return {}
    home = (
        codex_home
        or Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    ).resolve()
    try:
        file = Path(path).resolve()
        if not file.is_relative_to(home) or file.suffix != ".jsonl":
            return {}
        with file.open("rb") as stream:
            stream.seek(0, 2)
            start = max(0, stream.tell() - 4 * 1024 * 1024)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.read().splitlines()
        for line in reversed(lines):
            if b'"token_count"' not in line:
                continue
            try:
                event = json.loads(line)
                payload = event.get("payload", {})
                if (
                    event.get("type") != "event_msg"
                    or payload.get("type") != "token_count"
                ):
                    continue
                info = payload.get("info") or {}
                if not info.get("total_token_usage"):
                    continue

                def convert(values: dict) -> dict:
                    """Normalize rollout usage fields. 统一历史用量字段。"""
                    return {
                        "".join(
                            [k.split("_")[0]]
                            + [s.title() for s in k.split("_")[1:]]
                        ): v
                        for k, v in values.items()
                    }

                return {
                    "total": convert(info["total_token_usage"]),
                    "last": convert(info.get("last_token_usage", {})),
                    "modelContextWindow": info.get("model_context_window"),
                }
            except (ValueError, TypeError, AttributeError):
                continue
    except OSError:
        pass
    return {}
