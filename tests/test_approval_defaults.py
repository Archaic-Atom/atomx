"""Approval routing defaults, persistence and failure behavior."""
import tempfile
import unittest
from pathlib import Path

from textual.widgets import Switch

from arcatom_codex.demo import DemoClient
from arcatom_codex.preferences import approval_defaults, read_preferences, write_preferences
from arcatom_codex.rpc import RpcError
from arcatom_codex.ui import ArcatomApp


class ApprovalDefaultsTests(unittest.TestCase):
    def test_default_and_persisted_manual_routing_preserve_sandbox(self):
        self.assertEqual(approval_defaults({}), {
            "approvalPolicy": "on-request", "approvalsReviewer": "auto_review"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preferences.json"
            write_preferences({"approve_for_me": False}, path)
            settings = approval_defaults(read_preferences(path))
            self.assertEqual(settings, {"approvalPolicy": "on-request", "approvalsReviewer": "user"})
            self.assertNotIn("sandbox", settings)
            self.assertNotIn("sandboxPolicy", settings)


class ApprovalDefaultsUiTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_resume_and_setting_changes_route_through_backend(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await pilot.press("enter")
            await pilot.pause(.2)
            started = [p for method, p in client.calls if method == "thread/start"][-1]
            self.assertEqual(started["approvalsReviewer"], "auto_review")
            self.assertEqual(started["approvalPolicy"], "on-request")
            tid = app.current
            await pilot.press("f2")
            self.assertTrue(app.screen.query_one("#approve-for-me", Switch).value)
            app.screen.query_one("#approve-for-me", Switch).value = False
            await pilot.pause(.1)
            await pilot.press("escape")
            self.assertTrue(app.view_preferences.get("approve_for_me", True))
            await pilot.press("f2")
            app.screen.query_one("#approve-for-me", Switch).value = False
            await pilot.pause(.1)
            await pilot.press("ctrl+s")
            await pilot.pause(.2)
            self.assertFalse(app.view_preferences["approve_for_me"])
            await app.ensure_resumed(tid)
            changed = [p for method, p in client.calls if method == "thread/settings/update"][-1]
            self.assertEqual(changed, {"threadId": tid, "approvalPolicy": "on-request",
                                       "approvalsReviewer": "user"})
            await app.ensure_resumed("demo-dashboard")
            resumed = [p for method, p in client.calls if method == "thread/resume"][-1]
            self.assertEqual(resumed["approvalsReviewer"], "user")
            app.view_preferences["approve_for_me"] = True
            await app.ensure_resumed("demo-dashboard")
            self.assertEqual(app.store.get("demo-dashboard").meta["approvalsReviewer"], "auto_review")
            self.assertEqual(client.replies, [])

    async def test_backend_rejection_is_not_recorded_as_enabled(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            session = app.store.get("demo-dashboard")
            session.resumed = True
            session.approval_default_applied = False
            session.meta["approvalsReviewer"] = "user"
            original = client.call
            async def reject(method, params=None, timeout=30):
                if method == "thread/settings/update":
                    raise RpcError("Auto-review is unavailable")
                return await original(method, params, timeout)
            client.call = reject
            with self.assertRaises(RpcError):
                await app.ensure_resumed(session.id)
            self.assertFalse(session.approval_default_applied)
            self.assertEqual(session.meta["approvalsReviewer"], "user")
            self.assertFalse(any(method == "turn/start" for method, _ in client.calls))
