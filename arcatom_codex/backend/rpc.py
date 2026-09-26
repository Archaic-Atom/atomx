"""Local JSONL/WebSocket RPC transport. 本地协议传输，不执行命令或自动审批。"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

from websockets.asyncio.client import ClientConnection

from .. import __version__
from ..i18n import tr
from ..platform_support import executable_argv


class RpcError(RuntimeError):
    """A backend or transport error suitable for display. 可显示的后端或传输错误。"""

    def __init__(self, message: str, code: int | None = None) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        super().__init__(message)
        self.code = code


class CodexClient:
    """Correlate RPC calls and notifications on a local connection.

    管理本地请求和通知。
    """

    def __init__(
        self,
        binary: str = "codex",
        cwd: str | None = None,
        *,
        shared: bool = True,
    ) -> None:
        """Initialize local state without sending model requests. 初始化本地状态。"""
        self.binary, self.cwd = binary, cwd
        self.shared = shared
        self.events: asyncio.Queue[dict] = asyncio.Queue()
        self.pending: dict[int, asyncio.Future] = {}
        self.stderr: deque[str] = deque(maxlen=30)
        self.process: asyncio.subprocess.Process | None = None
        self.tasks: list[asyncio.Task] = []
        self.websocket: ClientConnection | None = None
        self.remote_endpoint: str | None = None
        self.server_version: str | None = None
        self.sequence = 0
        self.closing = False

    async def start(self) -> None:
        """Open the transport and initialize its protocol. 打开传输并初始化协议。"""
        self.closing = False
        env = dict(os.environ, NO_COLOR="1")
        argv = executable_argv(self.binary)
        transport = ["--stdio"]
        if self.shared and os.name != "nt":
            from .shared_backend import ensure_local_server

            endpoint = await asyncio.to_thread(
                ensure_local_server, self.binary, self.cwd
            )
            from websockets.asyncio.client import unix_connect

            # Bound the local close handshake without stopping the shared server.
            # 限制本地断开握手的等待时间，不终止共享后台服务。
            self.websocket = await unix_connect(
                endpoint, max_size=32 * 1024 * 1024, close_timeout=1
            )
            self.remote_endpoint = "unix://" + endpoint
            self.tasks = [asyncio.create_task(self._read())]
            await self.initialize()
            return
        elif self.shared:
            starter = await asyncio.create_subprocess_exec(
                *argv,
                "app-server",
                "daemon",
                "start",
                cwd=self.cwd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                output, errors = await asyncio.wait_for(
                    starter.communicate(), 30
                )
            except asyncio.TimeoutError:
                starter.kill()
                await starter.wait()
                raise RpcError(
                    tr(
                        "共享 Codex 服务启动超时，请检查 codex app-server daemon。"
                    )
                )
            if starter.returncode:
                raise RpcError(
                    tr("无法连接共享 Codex 服务：")
                    + (errors or output).decode(errors="replace")[-1600:]
                )
            transport = ["proxy"]
            self.remote_endpoint = "unix://"
        self.process = await asyncio.create_subprocess_exec(
            *argv,
            "app-server",
            *transport,
            cwd=self.cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024,
        )
        self.tasks = [
            asyncio.create_task(self._read()),
            asyncio.create_task(self._errors()),
        ]
        await self.initialize()

    async def initialize(self) -> None:
        """Send the client identity and initialized notification.

        发送客户端标识与初始化通知。
        """
        self.server_version = None
        response = await self.call(
            "initialize",
            {
                "clientInfo": {
                    "name": "arcatom_codex",
                    "title": "AtomX",
                    "version": __version__,
                },
                "capabilities": {"experimentalApi": True},
            },
        )
        # The leading version belongs to Codex; the trailing one is ours.
        # userAgent 开头是后端版本，末尾括号内才是客户端版本。
        user_agent = (
            response.get("userAgent") if isinstance(response, dict) else None
        )
        match = (
            re.match(
                r"^[^/\s]+/(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)(?:\s|$)",
                user_agent or "",
            )
            if isinstance(user_agent, str)
            else None
        )
        self.server_version = match.group(1) if match else None
        await self.send({"method": "initialized", "params": {}})

    async def send(self, message: dict) -> None:
        """Write one JSON message to the active transport. 向当前连接写入一条消息。"""
        if self.websocket is not None:
            await self.websocket.send(json.dumps(message, ensure_ascii=False))
            return
        if not self.process or self.process.returncode is not None:
            raise RpcError(tr("Codex 连接已关闭"))
        assert self.process.stdin is not None
        self.process.stdin.write(
            (json.dumps(message, ensure_ascii=False) + "\n").encode()
        )
        try:
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise RpcError(tr("Codex 连接已关闭")) from exc

    async def call(
        self, method: str, params: dict | None = None, timeout: float = 30
    ) -> Any:
        """Await one correlated RPC result with a timeout. 等待对应请求结果并限制超时。"""
        self.sequence += 1
        request_id = self.sequence
        future = asyncio.get_running_loop().create_future()
        self.pending[request_id] = future
        try:
            await self.send(
                {"id": request_id, "method": method, "params": params or {}}
            )
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError as exc:
            # Mutating calls must NOT be retried automatically: the server may have accepted them.
            # 写请求超时后不能自动重试，服务端可能已经接受。
            raise RpcError(
                tr("{0} 响应超时；请先检查会话状态，避免重复发送").format(
                    method
                )
            ) from exc
        finally:
            self.pending.pop(request_id, None)

    async def reply(self, request_id: Any, result: dict) -> None:
        """Answer one pending backend request. 回复后端待处理请求。"""
        await self.send({"id": request_id, "result": result})

    async def reject_unsupported(self, request_id: Any) -> None:
        """Reject a request that this client cannot safely handle. 拒绝未支持的请求。"""
        await self.send(
            {
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": "This client does not support this request; no approval granted.",
                },
            }
        )

    async def _read(self) -> None:
        """Dispatch responses and notifications until transport closure.

        读取并分发响应与通知。
        """
        try:
            while True:
                if self.websocket is not None:
                    from websockets.exceptions import ConnectionClosed

                    try:
                        line = await self.websocket.recv()
                    except ConnectionClosed:
                        break
                else:
                    assert (
                        self.process is not None
                        and self.process.stdout is not None
                    )
                    line = await self.process.stdout.readline()
                    if not line:
                        break
                message = json.loads(line)
                if "method" in message:
                    await self.events.put(message)
                else:
                    future = self.pending.get(message.get("id"))
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(
                                RpcError(
                                    error.get("message", str(error)),
                                    error.get("code"),
                                )
                            )
                        else:
                            future.set_result(message.get("result", {}))
        except (ValueError, OSError) as exc:
            self.stderr.append(str(exc))
        finally:
            await asyncio.sleep(0.05)
            error = RpcError(
                tr("Codex 服务已退出。") + "\n".join(self.stderr)[-1600:]
            )
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(error)
            if not self.closing:
                await self.events.put(
                    {
                        "method": "client/disconnected",
                        "params": {"message": str(error)},
                    }
                )

    async def _errors(self) -> None:
        """Collect bounded backend error output. 收集有限长度的后端错误输出。"""
        assert self.process is not None and self.process.stderr is not None
        while line := await self.process.stderr.readline():
            self.stderr.append(line.decode(errors="replace").rstrip())

    async def close(self) -> None:
        """Close this client connection and cancel its reader tasks.

        关闭当前客户端并取消读取任务。
        """
        self.closing = True
        if self.websocket is not None:
            await self.websocket.close()
        if self.process and self.process.returncode is None:
            if self.process.stdin is not None:
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
        self.websocket = None

    async def pages(
        self, method: str, params: dict
    ) -> AsyncIterator[list[dict]]:
        """Yield all response pages using server cursors. 使用服务端游标迭代分页结果。"""
        cursor = None
        seen = set()
        while True:
            response = await self.call(method, dict(params, cursor=cursor))
            yield response.get("data", [])
            cursor = response.get("nextCursor")
            if not cursor or cursor in seen:
                break
            seen.add(cursor)
