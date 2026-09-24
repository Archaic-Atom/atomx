"""Local app-server JSONL transport. Never interpret shell commands or auto-approve."""
from __future__ import annotations

import asyncio
from collections import deque
import json
import os
from typing import Any


class RpcError(RuntimeError):
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


class CodexClient:
    def __init__(self, binary: str = "codex", cwd: str | None = None):
        self.binary, self.cwd = binary, cwd
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.pending: dict[int, asyncio.Future] = {}
        self.stderr: deque[str] = deque(maxlen=30)
        self.process: asyncio.subprocess.Process | None = None
        self.tasks: list[asyncio.Task] = []
        self.sequence = 0
        self.closing = False

    async def start(self):
        env = dict(os.environ, NO_COLOR="1")
        self.process = await asyncio.create_subprocess_exec(
            self.binary, "app-server", "--stdio", cwd=self.cwd, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=32 * 1024 * 1024,
        )
        self.tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._errors())]
        await self.call("initialize", {
            "clientInfo": {"name": "arcatom_codex", "title": "Arcatom Codex", "version": "0.1.0"},
            "capabilities": {"experimentalApi": True},
        })
        await self.send({"method": "initialized", "params": {}})

    async def send(self, message: dict):
        if not self.process or self.process.returncode is not None:
            raise RpcError("Codex 连接已关闭")
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        try:
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise RpcError("Codex 连接已关闭") from exc

    async def call(self, method: str, params: dict | None = None, timeout: float = 30) -> Any:
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send({"id": request_id, "method": method, "params": params or {}})
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            # Mutating calls must NOT be retried automatically: the server may have accepted them.
            raise RpcError(f"{method} 响应超时；请先检查会话状态，避免重复发送") from exc
        finally:
            self.pending.pop(request_id, None)

    async def reply(self, request_id: Any, result: dict):
        await self.send({"id": request_id, "result": result})

    async def reject_unsupported(self, request_id: Any):
        await self.send({"id": request_id, "error": {
            "code": -32601, "message": "This client does not support this request; no approval granted."
        }})

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if "method" in message:
                    await self.events.put(message)
                else:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(RpcError(error.get("message", str(error)), error.get("code")))
                        else:
                            future.set_result(message.get("result", {}))
        except (ValueError, OSError) as exc:
            self.stderr.append(str(exc))
        finally:
            await asyncio.sleep(0.05)
            error = RpcError("Codex 服务已退出。" + "\n".join(self.stderr)[-1600:])
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
            if not self.closing:
                await self.events.put({"method": "client/disconnected", "params": {"message": str(error)}})

    async def _errors(self):
        while line := await self.process.stderr.readline():
            self.stderr.append(line.decode(errors="replace").rstrip())

    async def close(self):
        self.closing = True
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except asyncio.TimeoutError:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 3)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    async def pages(self, method: str, params: dict):
        cursor = None
        seen = set()
        while True:
            response = await self.call(method, dict(params, cursor=cursor))
            yield response.get("data", [])
            cursor = response.get("nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
