"""Terminal title status, sanitization and ownership. 标题状态、净化与恢复测试。"""

import tempfile
import unittest
from unittest.mock import patch

from arcatom_codex.demo import DemoClient
from arcatom_codex.state import Session
from arcatom_codex.ui import AtomXApp
from arcatom_codex.window_title import build_title, title_driver, write_title


class WindowTitleTests(unittest.TestCase):
    """Check status labels and terminal title ownership. 检查状态与标题持有。"""

    def test_waiting_overrides_animation_and_modes_preserve_status(
        self,
    ) -> None:
        """Show attention before running status in every mode. 等待确认优先显示。"""
        session = Session("a", meta={"name": "Fix login", "model": "Model A"})
        session.active_turn = "turn"

        def render(tick: int, mode: str = "session") -> str:
            """Render one deterministic animation frame. 渲染确定的动画帧。"""
            return build_title(
                session, ready=True, sending=False, mode=mode, tick=tick
            )

        self.assertNotEqual(render(0), render(1))
        self.assertIn("Fix login", render(0))
        self.assertIn("Model A", render(0, "model"))
        self.assertNotIn("Fix login", render(0, "app"))
        session.pending_requests.add("approval")
        self.assertEqual(render(0), render(1))
        self.assertTrue(render(0).startswith("!"))
        session.pending_requests.clear()
        session.active_turn = None
        session.last_turn_status = "completed"
        self.assertTrue(render(0).startswith("✓"))
        session.last_turn_status = "failed"
        self.assertTrue(render(0).startswith("!"))
        session.last_turn_status = "interrupted"
        self.assertTrue(render(0).startswith("■"))

    def test_title_restore_is_paired_and_same_title_is_rewritten_after_resume(
        self,
    ) -> None:
        """Restore on every handoff and block control injection. 接管成对恢复并阻止注入。"""
        base = AtomXApp.get_driver_class(None)
        wrapped = title_driver(base)
        driver = object.__new__(wrapped)
        with (
            patch.object(base, "start_application_mode"),
            patch.object(base, "stop_application_mode"),
            patch.object(base, "flush"),
            patch.object(base, "write") as write,
        ):
            for _ in range(2):
                driver.start_application_mode()
                write_title(driver, "AtomX\x1b]2;injected\x07\x9c\n title")
                write_title(driver, "AtomX\x1b]2;injected\x07\x9c\n title")
                driver.stop_application_mode()
                write_title(driver, "must not write while suspended")
            driver.stop_application_mode()
            self.assertEqual(
                [c.args[0] for c in write.call_args_list],
                [
                    "\x1b[22;0t",
                    "\x1b]0;AtomX title\x07",
                    "\x1b[23;0t",
                    "\x1b[22;0t",
                    "\x1b]0;AtomX title\x07",
                    "\x1b[23;0t",
                ],
            )


class WindowTitleUiTests(unittest.IsolatedAsyncioTestCase):
    """Check title changes across workspace events. 检查工作台事件驱动标题。"""

    async def test_title_tracks_events_and_resets_on_home(self) -> None:
        """Refresh without transcript changes and reset on navigation. 独立刷新并返回重置。"""
        app = AtomXApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(0.2)
            await pilot.press("enter")
            tid = app.current
            app.store.event(
                "turn/started", {"threadId": tid, "turn": {"id": "t"}}
            )
            app.paint()
            self.assertIn(app.store.get(tid).title, app.title)
            app.store.get(tid).pending_requests.add("approval")
            app.paint()
            self.assertTrue(app.title.startswith("!"))
            app.store.get(tid).pending_requests.clear()
            app.store.event(
                "turn/completed", {"threadId": tid, "turn": {"id": "t"}}
            )
            app.paint()
            self.assertTrue(app.title.startswith("✓"))
            title = app.store.get(tid).title
            app.show_home()
            self.assertNotIn(title, app.title)
            app.ready = False
            app.paint()
            self.assertTrue(app.title.startswith("○"))
