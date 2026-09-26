"""End-to-end MCP form requests. MCP 表单请求的端到端验证。"""

import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import Button, Input, Select, SelectionList, Switch

from arcatom_codex.demo import DemoClient
from arcatom_codex.elicitation import (
    ElicitationForm,
    ElicitationLink,
    form_fields,
)
from arcatom_codex.ui import ArcatomApp


def request(request_id: int, schema: dict, mode: str = "form") -> dict:
    """Build a backend request without model access. 构造本地后端请求。"""
    return {
        "id": request_id,
        "method": "mcpServer/elicitation/request",
        "params": {
            "threadId": "demo-dashboard",
            "turnId": "test-turn",
            "serverName": "Sample extension",
            "mode": mode,
            "message": "Provide the requested details",
            "requestedSchema": schema,
        },
    }


class ElicitationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise explicit actions and structured replies. 验证明确操作及结构化回包。"""

    async def test_form_requires_explicit_submit_and_returns_typed_content(
        self,
    ) -> None:
        """Validate all primitive controls and exact RPC response.

        验证基础控件、必填校验和准确的 RPC 回复。
        """
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        schema = {
            "type": "object",
            "properties": {
                "name": {"type": "string", "title": "Name", "minLength": 2},
                "count": {"type": "integer", "minimum": 1, "maximum": 5},
                "enabled": {"type": "boolean", "default": False},
                "choice": {
                    "type": "string",
                    "oneOf": [
                        {"const": "a", "title": "Choice A"},
                        {"const": "b", "title": "Choice B"},
                    ],
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["x", "y"]},
                    "minItems": 1,
                },
            },
            "required": ["name", "choice", "tags"],
        }
        async with app.run_test(size=(90, 35)) as pilot:
            await pilot.pause(0.2)
            await client.events.put(request(17, schema))
            await pilot.pause(0.2)
            form = app.screen
            self.assertIsInstance(form, ElicitationForm)
            self.assertEqual(client.replies, [])
            form.query_one("#form-accept", Button).press()
            await pilot.pause(0.1)
            self.assertIs(app.screen, form)
            self.assertTrue(form.query_one("#form-error").content)
            form.query_one("#field-0", Input).value = "Alex"
            form.query_one("#field-1", Input).value = "3"
            form.query_one("#field-2", Switch).value = True
            form.query_one("#field-3", Select).value = "b"
            form.query_one("#field-4", SelectionList).select("x")
            form.query_one("#form-accept", Button).press()
            await pilot.pause(0.2)
            self.assertEqual(
                client.replies[-1],
                (
                    17,
                    {
                        "action": "accept",
                        "content": {
                            "name": "Alex",
                            "count": 3,
                            "enabled": True,
                            "choice": "b",
                            "tags": ["x"],
                        },
                        "_meta": None,
                    },
                ),
            )
            self.assertFalse(app.store.get("demo-dashboard").pending_requests)

    async def test_cancel_decline_and_unsupported_schema_never_accept(
        self,
    ) -> None:
        """Keep rejection and cancellation distinct. 区分拒绝与取消。"""
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        schema = {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
        }
        async with app.run_test(size=(80, 28)) as pilot:
            await pilot.pause(0.2)
            await client.events.put(request(1, schema, mode="openai/form"))
            await pilot.pause(0.2)
            await pilot.press("escape")
            await pilot.pause(0.15)
            self.assertEqual(client.replies[-1][1]["action"], "cancel")
            await client.events.put(request(2, schema, mode="openaiForm"))
            await pilot.pause(0.2)
            app.screen.query_one("#form-decline", Button).press()
            await pilot.pause(0.15)
            self.assertEqual(client.replies[-1][1]["action"], "decline")
            unknown = {
                "type": "object",
                "properties": {"nested": {"type": "object", "properties": {}}},
            }
            await client.events.put(request(3, unknown))
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ElicitationForm)
            self.assertFalse(app.screen.query("#form-accept"))
            await pilot.press("escape")
            await pilot.pause(0.15)
            self.assertEqual(client.replies[-1][1]["action"], "cancel")

    async def test_link_requires_user_completion_before_accept(self) -> None:
        """Copy a URL without automatically accepting it.

        复制链接不代表完成；只有用户选择已完成才提交。
        """
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        url = "https://example.com/connect?token=sample"
        params = request(4, {})
        params["params"] = {
            "threadId": "demo-dashboard",
            "mode": "url",
            "serverName": "Sample extension",
            "message": "Connect your account",
            "url": url,
            "elicitationId": "link-1",
        }
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.2)
            await client.events.put(params)
            await pilot.pause(0.2)
            self.assertIsInstance(app.screen, ElicitationLink)
            with patch.object(app, "copy_to_clipboard") as copy:
                app.screen.query_one("#link-copy", Button).press()
                await pilot.pause(0.1)
                copy.assert_called_once_with(url)
            self.assertFalse(client.replies)
            app.screen.query_one("#link-accept", Button).press()
            await pilot.pause(0.15)
            self.assertEqual(
                client.replies[-1],
                (
                    4,
                    {
                        "action": "accept",
                        "content": None,
                        "_meta": None,
                    },
                ),
            )

    def test_unrecognized_required_or_nested_schema_is_not_guessed(
        self,
    ) -> None:
        """Reject unknown structures before making controls.

        遇到未知字段结构时不猜测值。
        """
        self.assertIsNone(
            form_fields(
                {
                    "mode": "form",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"x": {"type": "string"}},
                        "required": [["x"]],
                    },
                }
            )
        )
        self.assertIsNone(
            form_fields(
                {
                    "mode": "form",
                    "requestedSchema": {
                        "type": "object",
                        "properties": {"x": {"type": "object"}},
                    },
                }
            )
        )
