"""Backend version provenance and reconnect handling. 后端版本来源及重连处理。"""

import unittest
from unittest.mock import AsyncMock, patch

from arcatom_codex import __version__
from arcatom_codex.rpc import CodexClient


class ServerVersionTests(unittest.IsolatedAsyncioTestCase):
    """Distinguish the backend version from our trailing client version. 区分后端与客户端版本。"""

    async def test_handshake_version_and_unknown_reconnect(self) -> None:
        """Read the server token and clear it when a new handshake omits it.

        读取服务端标识开头的版本，重连缺失版本时不保留旧值。
        """
        client = CodexClient()
        cases = [
            (
                {
                    "userAgent": (
                        "arcatom_codex/0.156.1 (Mac OS 27.0.0; arm64) "
                        "iTerm.app/3.7.2 (arcatom_codex; 0.1.0)"
                    )
                },
                "0.156.1",
            ),
            (
                {"userAgent": "codex_cli_rs/0.157.0-alpha.2 (Windows 11)"},
                "0.157.0-alpha.2",
            ),
            ({"userAgent": "codex_cli_rs/0.156.1"}, "0.156.1"),
            ({"userAgent": "unrecognized (client; 0.1.0)"}, None),
            ({"userAgent": 123}, None),
            ({}, None),
            (None, None),
        ]
        for response, expected in cases:
            with self.subTest(response=response):
                client.server_version = "old-version"
                with (
                    patch.object(
                        client, "call", new=AsyncMock(return_value=response)
                    ) as call,
                    patch.object(client, "send", new=AsyncMock()) as send,
                ):
                    await client.initialize()
                    self.assertEqual(client.server_version, expected)
                    self.assertEqual(
                        call.call_args.args[1]["clientInfo"]["version"],
                        __version__,
                    )
                    send.assert_awaited_once_with(
                        {"method": "initialized", "params": {}}
                    )
