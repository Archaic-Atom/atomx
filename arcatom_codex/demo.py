"""Explicit offline demo and deterministic protocol fixture for UI tests."""
import asyncio
import copy
import time
import uuid

from .rpc import RpcError


class DemoClient:
    def __init__(self):
        self.events = asyncio.Queue()
        self.calls = []
        self.replies = []
        self.tasks = []
        self.threads = {}
        now = int(time.time())
        for tid, title, directory, age in [
            ("demo-login", "修复登录问题", "~/Projects/atlas", 0),
            ("demo-dashboard", "整理用量统计界面", "~/Projects/dashboard", 3500),
            ("demo-readme", "给项目写一份清晰的 README", "~/Projects/arcatom", 86400),
        ]:
            self.threads[tid] = {"id": tid, "name": title, "preview": title, "cwd": directory,
                "createdAt": now-age, "updatedAt": now-age, "status": {"type": "idle"},
                "model": "Codex · 演示", "source": "appServer", "turns": [], "parentThreadId": None}
        self.threads["demo-login"]["turns"] = [{"id": "turn-demo", "status": "completed", "items": [
            {"id": "u1", "type": "userMessage", "content": [{"type": "text", "text": "请检查登录流程，让两个子代理分别检查后端和前端。"}]},
            {"id": "a1", "type": "agentMessage", "text": "我已把检查分给两个子代理。\n\n后端已确认 token 刷新逻辑；前端正在检查登录后的跳转。你可以在下方展开 **代理与进程** 查看详情。"},
            {"id": "collab1", "type": "collabAgentToolCall", "tool": "spawnAgent", "status": "completed",
             "senderThreadId": "demo-login", "receiverThreadIds": ["demo-backend", "demo-frontend"],
             "agentsStates": {"demo-backend": {"status": "completed"}, "demo-frontend": {"status": "running"}}},
            {"id": "command1", "type": "commandExecution", "command": "npm test -- login", "status": "completed", "exitCode": 0,
             "aggregatedOutput": "PASS login.test.ts\n✓ 登录成功后跳转\n✓ 过期 token 刷新\nTests: 2 passed"},
            {"id": "command2", "type": "commandExecution", "command": "npm run dev", "status": "inProgress", "processId": "321", "aggregatedOutput": "Local: http://localhost:5173/\nReady in 280ms"},
        ]}]
        for tid, name in [("demo-backend", "后端检查"), ("demo-frontend", "前端检查")]:
            self.threads[tid] = {"id": tid, "name": name, "agentNickname": name, "preview": name,
                "cwd": "~/Projects/atlas", "updatedAt": now, "parentThreadId": "demo-login", "source": {"subAgent": "other"},
                "status": {"type": "idle" if tid == "demo-backend" else "active"},
                "turns": [{"id": "child-turn", "status": "completed", "items": [
                    {"id": "child-answer", "type": "agentMessage", "text": "已找到对应代码，正在核对登录边界条件。"}]}]}

    async def start(self):
        for tid, total in [("demo-login", 28100), ("demo-dashboard", 12480), ("demo-readme", 1600)]:
            await self.events.put({"method": "thread/tokenUsage/updated", "params": {
                "threadId": tid, "turnId": "demo-history", "tokenUsage": {
                    "total": {"totalTokens": total, "inputTokens": total-600, "outputTokens": 600,
                              "cachedInputTokens": max(0, total-2600)},
                    "last": {"totalTokens": 1800}, "modelContextWindow": 128000}}})

    async def close(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def pages(self, method, params):
        yield (await self.call(method, params)).get("data", [])

    async def call(self, method, params=None, timeout=30):
        params = params or {}
        self.calls.append((method, copy.deepcopy(params)))
        if method == "thread/list":
            parent = params.get("ancestorThreadId")
            threads = [t for t in self.threads.values() if t.get("parentThreadId") == parent]
            return {"data": [{k: copy.deepcopy(v) for k, v in t.items() if k != "turns"}
                             for t in threads], "nextCursor": None}
        if method == "thread/items/list":
            entries = [{"item": copy.deepcopy(item), "turnId": turn["id"]}
                       for turn in self.threads[params["threadId"]]["turns"]
                       for item in turn.get("items", [])]
            if params.get("sortDirection") == "desc":
                entries.reverse()
            offset, limit = int(params.get("cursor") or 0), params.get("limit", 40)
            page = entries[offset:offset + limit]
            return {"data": page, "nextCursor": str(offset + limit) if offset + limit < len(entries) else None}
        if method == "model/list":
            return {"data": [{"id": name, "model": name, "displayName": label,
                "description": "离线演示模型", "hidden": False, "isDefault": i == 0,
                "defaultReasoningEffort": "medium", "supportedReasoningEfforts": [
                    {"reasoningEffort": "low", "description": "快速响应"},
                    {"reasoningEffort": "medium", "description": "平衡"},
                    {"reasoningEffort": "high", "description": "深入思考"}],
                "serviceTiers": [{"id": "fast", "name": "Fast", "description": "演示快速档位"}]}
                for i, (name, label) in enumerate([("demo-codex", "Codex · 演示"), ("demo-reasoner", "Reasoner · 演示")])], "nextCursor": None}
        if method == "thread/settings/update":
            meta = self.threads[params["threadId"]]
            meta.update({("reasoningEffort" if k == "effort" else k): v for k, v in params.items() if k != "threadId"})
            return {}
        if method == "permissionProfile/list":
            return {"data": [{"id": "read-only", "allowed": True, "description": "只读"},
                {"id": "workspace-write", "allowed": True, "description": "项目内读写"}]}
        if method == "skills/list":
            return {"data": [{"skills": [], "errors": []}]}
        if method == "mcpServerStatus/list":
            return {"data": []}
        if method == "config/read":
            return {"config": {"model": "demo-codex"}, "layers": []}
        if method in ("thread/compact/start", "review/start"):
            self.tasks.append(asyncio.create_task(self.respond(params["threadId"], "演示：" + method)))
            return {}
        if method == "thread/fork":
            meta = copy.deepcopy(self.threads[params["threadId"]])
            meta["id"] = "demo-" + str(uuid.uuid4())
            meta["name"] += " · 分支"
            self.threads[meta["id"]] = meta
            return {"thread": copy.deepcopy(meta), "model": meta.get("model", "demo-codex")}
        if method == "thread/archive":
            self.threads.pop(params["threadId"], None)
            return {}
        if method in ("thread/backgroundTerminals/clean", "thread/unsubscribe"):
            return {}
        if method.startswith("thread/goal/"):
            meta = self.threads[params["threadId"]]
            if method.endswith("/set"):
                meta.setdefault("goal", {}).update({k: v for k, v in params.items() if k != "threadId"})
            elif method.endswith("/clear"):
                meta.pop("goal", None)
            return {"goal": meta.get("goal")}
        if method in ("thread/read", "thread/resume"):
            thread = self.threads.get(params["threadId"])
            if not thread:
                raise RpcError("会话不存在")
            metadata = copy.deepcopy(thread)
            if params.get("excludeTurns") or (method == "thread/read" and not params.get("includeTurns")):
                metadata.pop("turns", None)
            return {"thread": metadata, "model": thread.get("model") if thread.get("model") in ("demo-codex", "demo-reasoner") else "demo-codex",
                    "reasoningEffort": thread.get("reasoningEffort", "medium"), "collaborationMode": thread.get("collaborationMode")}
        if method == "thread/start":
            tid = "demo-" + str(uuid.uuid4())
            thread = {"id": tid, "name": "新会话", "cwd": params["cwd"], "status": {"type": "idle"},
                      "model": "Codex · 演示", "source": "appServer", "updatedAt": int(time.time()), "turns": []}
            self.threads[tid] = thread
            return {"thread": copy.deepcopy(thread)}
        if method == "thread/backgroundTerminals/list":
            return {"data": [{"processId": "321", "itemId": "command2", "command": "npm run dev", "osPid": 4321}]
                    if params["threadId"] == "demo-login" else []}
        if method == "account/usage/read":
            return {"summary": {"lifetimeTokens": 1248300}, "dailyUsageBuckets": [{"startDate": "演示当天", "tokens": 42180}]}
        if method == "account/rateLimits/read":
            return {"rateLimits": {"primary": {"usedPercent": 24, "windowDurationMins": 300}, "secondary": {"usedPercent": 37, "windowDurationMins": 10080}}}
        if method == "thread/name/set":
            self.threads[params["threadId"]]["name"] = params["name"]
            return {}
        if method == "thread/delete":
            ids = {params["threadId"]}
            while True:
                children = {tid for tid, meta in self.threads.items() if meta.get("parentThreadId") in ids}
                if children <= ids:
                    break
                ids.update(children)
            for tid in ids:
                self.threads.pop(tid, None)
                await self.events.put({"method": "thread/deleted", "params": {"threadId": tid}})
            return {}
        if method in ("turn/start", "turn/steer"):
            task = asyncio.create_task(self.respond(params["threadId"], params["input"], params.get("clientUserMessageId")))
            self.tasks.append(task)
            return {"turn": {"id": "demo-response", "status": "inProgress"}}
        if method == "turn/interrupt":
            for task in self.tasks:
                task.cancel()
            await self.events.put({"method": "turn/completed", "params": {"threadId": params["threadId"], "turn": {"id": params["turnId"], "status": "interrupted"}}})
            return {}
        raise RpcError("演示不支持此方法：" + method, -32601)

    async def reply(self, rid, result):
        self.replies.append((rid, result))

    async def reject_unsupported(self, rid):
        self.replies.append((rid, {"error": "unsupported"}))

    async def respond(self, tid, content, client_id=None):
        async def emit(method, **params):
            await self.events.put({"method": method, "params": dict(threadId=tid, **params)})
        uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
        await emit("turn/started", turn={"id": "demo-response", "status": "inProgress"})
        await emit("item/completed", item={"id": uid, "type": "userMessage", "content": content, "clientId": client_id})
        await emit("item/started", item={"id": aid, "type": "agentMessage", "text": ""})
        text = "这是离线演示回复。实际模式会把你的消息发送给本机 Codex，并实时显示回复、子代理和命令状态。"
        for i in range(0, len(text), 5):
            await asyncio.sleep(0.04)
            await emit("item/agentMessage/delta", itemId=aid, delta=text[i:i+5])
        await emit("item/completed", item={"id": aid, "type": "agentMessage", "text": text})
        await emit("thread/tokenUsage/updated", turnId="demo-response", tokenUsage={"total": {"totalTokens": 28100, "inputTokens": 25000, "outputTokens": 3100, "cachedInputTokens": 18000}, "last": {"totalTokens": 8100}, "modelContextWindow": 128000})
        await emit("turn/completed", turn={"id": "demo-response", "status": "completed"})
