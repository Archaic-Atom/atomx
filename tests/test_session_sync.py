"""Loading, navigation and shared-event regressions. 会话加载和同步回归。"""
import asyncio
import tempfile
import unittest
from unittest.mock import patch

from arcatom_codex.demo import DemoClient
from arcatom_codex.rpc import RpcError
from arcatom_codex.ui import ArcatomApp, Composer, Detail


class DelayedHistory(DemoClient):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, method, params=None, timeout=30):
        if method == "thread/items/list" and params["threadId"] == "demo-login":
            self.entered.set()
            await self.release.wait()
        return await super().call(method, params, timeout)


class SessionSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_typing_from_transcript_and_activity_keeps_first_letter_and_slash(self):
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            await pilot.press("escape", "h", "i")
            composer = app.query_one(Composer)
            self.assertTrue(composer.has_focus)
            self.assertEqual(composer.text, "hi")
            composer.clear()
            await pilot.press("ctrl+t", "x")
            self.assertTrue(composer.has_focus)
            self.assertEqual(composer.text, "x")
            composer.clear()
            await pilot.press("escape", "/", "s", "t", "a", "t", "u", "s", "enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Detail)
            await pilot.press("q")
            self.assertIsInstance(app.screen, Detail)
            self.assertFalse(any(m == "turn/start" for m, _ in app.client.calls))

    async def test_history_loading_does_not_block_typing_or_steal_switched_focus(self):
        client = DelayedHistory()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await asyncio.wait_for(app.open_session("demo-login"), .2)
            await asyncio.wait_for(client.entered.wait(), 1)
            self.assertEqual(app.current, "demo-login")
            self.assertTrue(app.store.get("demo-login").history_loading)
            await pilot.press("d", "r", "a", "f", "t")
            await app.open_session("demo-dashboard")
            await pilot.press("n", "e", "w")
            client.release.set()
            await pilot.pause(.2)
            self.assertEqual(app.current, "demo-dashboard")
            self.assertEqual(app.query_one(Composer).text, "new")
            self.assertEqual(app.store.get("demo-login").draft, "draft")
            self.assertTrue(app.store.get("demo-login").hydrated)

    async def test_large_history_pages_in_order_and_cached_reopen_avoids_reload(self):
        client = DemoClient()
        client.threads["demo-login"]["turns"] = [{"id": "long", "status": "completed", "items": [
            {"id": f"m{i}", "type": "agentMessage", "text": f"Message {i}"}
            for i in range(95)]}]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            session = app.store.get("demo-login")
            self.assertEqual(list(session.items), [f"m{i}" for i in range(55, 95)])
            self.assertTrue(app.query_one("#older-history").display)
            app.load_older_history()
            await pilot.pause(.2)
            self.assertEqual(list(session.items), [f"m{i}" for i in range(15, 95)])
            app.load_older_history()
            await pilot.pause(.2)
            self.assertEqual(list(session.items), [f"m{i}" for i in range(95)])
            self.assertIsNone(session.history_cursor)
            calls = len([m for m, _ in client.calls if m in ("thread/read", "thread/resume", "thread/items/list")])
            app.show_home()
            with patch("arcatom_codex.view_actions.pretty", side_effect=AssertionError("cache lost")):
                await app.open_session("demo-login")
                await pilot.pause(.1)
            self.assertEqual(calls, len([m for m, _ in client.calls if m in ("thread/read", "thread/resume", "thread/items/list")]))
            self.assertFalse(any(m == "thread/read" for m, _ in client.calls))

    async def test_scrolling_to_top_loads_an_earlier_page(self) -> None:
        """Scroll-boundary paging exposes earlier messages without a click.

        滚动到顶部时自动读取更早一页，不依赖按钮。
        """
        client = DemoClient()
        client.threads["demo-login"]["turns"] = [{
            "id": "long", "status": "completed", "items": [
                {"id": f"m{i}", "type": "agentMessage", "text": f"Message {i}"}
                for i in range(95)
            ],
        }]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await app.open_session("demo-login")
            await pilot.pause(.2)
            session = app.store.get("demo-login")
            self.assertEqual(next(iter(session.items)), "m55")
            scroll = app.query_one("#transcript-scroll")
            self.assertGreater(scroll.max_scroll_y, 0)
            scroll.scroll_home(animate=False, immediate=True)
            await pilot.pause(.2)
            self.assertEqual(next(iter(session.items)), "m15")

    async def test_live_activity_does_not_push_visible_history_out(self) -> None:
        """Keep the oldest visible message while a long turn adds tools.

        长回合连续新增命令时，保留原本已显示的对话起点。
        """
        client = DemoClient()
        client.threads["demo-login"]["turns"] = [{
            "id": "long",
            "status": "completed",
            "items": [
                {"id": f"m{i}", "type": "agentMessage", "text": f"Message {i}"}
                for i in range(95)
            ],
        }]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await app.open_session("demo-login")
            await pilot.pause(.2)
            session = app.store.get("demo-login")
            self.assertEqual(session.visible_items, 40)
            self.assertEqual(next(iter(session.items)), "m55")
            for index in range(60):
                app.store.event("item/completed", {
                    "threadId": "demo-login",
                    "item": {
                        "id": f"tool-{index}",
                        "type": "commandExecution",
                        "command": f"probe {index}",
                    },
                })
            self.assertEqual(session.visible_items, 100)
            self.assertEqual(
                list(session.items)[-session.visible_items], "m55"
            )
            app.store.event("item/agentMessage/delta", {
                "threadId": "demo-login", "itemId": "reply", "delta": "Done",
            })
            self.assertEqual(session.visible_items, 101)
            app.load_older_history()
            await pilot.pause(.2)
            self.assertEqual(next(iter(session.items)), "m15")
            self.assertEqual(session.visible_items, len(session.items))

    async def test_streamed_completion_is_not_duplicated_by_history_page(self):
        client = DelayedHistory()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await client.entered.wait()
            original = client.threads["demo-login"]["turns"][0]["items"][1]["text"]
            await client.events.put({"method": "item/completed", "params": {
                "threadId": "demo-login", "item": {"id": "a1", "type": "agentMessage", "text": original + " final"}}})
            await pilot.pause(.1)
            client.release.set()
            await pilot.pause(.2)
            self.assertEqual(app.store.get("demo-login").items["a1"]["text"], original + " final")
            self.assertEqual(list(app.store.get("demo-login").items).count("a1"), 1)

    async def test_reconnect_replaces_old_tail_without_scrambling_order(self):
        client = DemoClient()
        items = [{"id": f"m{i}", "type": "agentMessage", "text": f"Message {i}"}
                 for i in range(95)]
        client.threads["demo-login"]["turns"] = [{"id": "long", "status": "completed", "items": items}]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            app.load_older_history()
            await pilot.pause(.2)
            items.append({"id": "m95", "type": "agentMessage", "text": "New elsewhere"})
            session = app.store.get("demo-login")
            session.resumed = False
            await app.open_session("demo-login")
            await pilot.pause(.2)
            self.assertEqual(list(session.items), [f"m{i}" for i in range(56, 96)])
            app.load_older_history()
            await pilot.pause(.2)
            self.assertEqual(list(session.items), [f"m{i}" for i in range(16, 96)])

    async def test_failed_history_can_retry_without_losing_draft(self):
        client = DemoClient()
        original = client.call
        async def fail_history(method, params=None, timeout=30):
            if method == "thread/items/list":
                raise RpcError("Temporary history error")
            return await original(method, params, timeout)
        client.call = fail_history
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.2)
            self.assertTrue(app.store.get("demo-login").history_error)
            await pilot.press("d")
            client.call = original
            await pilot.press("ctrl+r")
            await pilot.pause(.2)
            self.assertEqual(app.query_one(Composer).text, "d")
            self.assertFalse(app.store.get("demo-login").history_error)
            self.assertIn("a1", app.store.get("demo-login").items)
