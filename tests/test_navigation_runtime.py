"""Keyboard priorities, terminal background and agents. 导航、背景和多代理验证。"""
import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

from textual.widgets import OptionList
from textual.widgets._toast import Toast
from textual.notifications import Notification

from arcatom_codex.demo import DemoClient
from arcatom_codex.settings import Settings
from arcatom_codex.ui import ArcatomApp, Composer, Detail


class DelayedStart(DemoClient):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, method, params=None, timeout=30):
        if method == "turn/start":
            self.entered.set()
            await self.release.wait()
        return await super().call(method, params, timeout)


class NavigationRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_escape_closes_settings_then_interrupts_then_navigates(self):
        client = DemoClient()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            app.store.event("turn/started", {"threadId": session.id, "turn": {"id": "running"}})
            composer = app.query_one(Composer)
            composer.load_text("unsent draft")
            with patch("arcatom_codex.ui.copy_text", return_value=True):
                await pilot.press("ctrl+c")
                self.assertFalse(any(m == "turn/interrupt" for m, _ in client.calls))
            await pilot.press("f2")
            self.assertIsInstance(app.screen, Settings)
            await pilot.press("escape")
            self.assertIs(app.screen, app.main_screen)
            self.assertFalse(any(m == "turn/interrupt" for m, _ in client.calls))
            await pilot.press("escape")
            await pilot.pause(.2)
            stops = [p for m, p in client.calls if m == "turn/interrupt"]
            self.assertEqual(stops, [{"threadId": session.id, "turnId": "running"}])
            self.assertEqual(app.current, session.id)
            self.assertTrue(composer.has_focus)
            self.assertEqual(composer.text, "unsent draft")
            await pilot.press("escape")
            self.assertTrue(app.query_one("#transcript-scroll").has_focus)
            await pilot.press("escape")
            self.assertIsNone(app.current)
            self.assertTrue(app.query_one("#sessions").has_focus)

    async def test_escape_while_turn_start_is_in_flight_stops_on_started_event(self):
        client = DelayedStart()
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            await pilot.press("g", "o", "enter")
            await asyncio.wait_for(client.entered.wait(), 2)
            await pilot.press("escape", "escape")
            self.assertEqual(app.current, "demo-dashboard")
            client.release.set()
            await pilot.pause(.3)
            self.assertEqual(sum(m == "turn/interrupt" for m, _ in client.calls), 1)
            self.assertFalse(app.stop_requested)
            self.assertIsNone(app.store.get(app.current).active_turn)

    async def test_all_themes_preserve_terminal_background_on_surfaces(self):
        with patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        self.assertFalse(app.no_color)
        async with app.run_test() as pilot:
            await pilot.pause(.2)
            self.assertTrue(app.ansi_color)
            self.assertTrue(app.screen.styles.background.rich_color.is_default)
            self.assertTrue(app.query_one("#sessions").styles.background.rich_color.is_default)
            # Check the actual emitted background, not just a CSS declaration.
            # 检查渲染结果的默认背景，确保没有变回固定 RGB 黑色。
            update = app.screen._compositor.render_full_update()
            segments = [segment for line in update.strips for strip in line for segment in strip]
            self.assertTrue(any(segment.style and segment.style.bgcolor and
                                segment.style.bgcolor.is_default for segment in segments))
            # Headless Textual omits the toast rack; mount its real widget directly.
            # 无窗口测试不创建通知容器，直接挂载真实提示组件。
            await app.screen.mount(Toast(Notification("Restart arcatom to apply", timeout=30)))
            for theme in ("paper", "warm", "midnight", "forest", "gray"):
                app.apply_appearance({"ui_theme": theme})
                await pilot.pause(.1)
                for widget in (app.screen, *app.query("#sessions, #search, #bottom, #composer, Toast")):
                    self.assertTrue(widget.styles.background.rich_color.is_default,
                                    (theme, widget))
                # Check real painted surfaces, including the notification's border.
                # 检查实际绘制的输入框和提示，避免仅改 CSS 而遗漏内部样式。
                for selector in ("#search", "#bottom", "Toast"):
                    widget = app.query_one(selector)
                    for strip in widget.render_lines(widget.size.region):
                        for segment in strip:
                            background = segment.style.bgcolor if segment.style else None
                            self.assertTrue(background is None or background.is_default,
                                            (theme, selector, segment))
                await app.open_session("demo-dashboard")
                await pilot.pause(.1)
                composer = app.query_one(Composer)
                composer.cursor_blink = False
                composer.load_text("first line\nsecond line")
                await pilot.pause(.1)
                filled_cells = 0
                for strip in composer.render_lines(composer.size.region):
                    for segment in strip:
                        background = segment.style.bgcolor if segment.style else None
                        if background and not background.is_default:
                            filled_cells += segment.cell_length
                self.assertLessEqual(filled_cells, 1, (theme, "only the caret may be filled"))
                app.show_home()
                await pilot.pause(.1)

    async def test_twelve_agents_are_individually_accessible_and_update_status(self):
        client = DemoClient()
        for i in range(12):
            client.threads[f"worker-{i}"] = {
                "id": f"worker-{i}", "name": f"Worker {i}",
                "parentThreadId": "demo-dashboard", "status": {"type": "active"},
                "turns": [{"id": f"turn-{i}", "status": "inProgress", "items": [
                    {"id": f"answer-{i}", "type": "agentMessage", "text": f"Output from worker {i}"}]}]}
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.3)
            await pilot.press("ctrl+t")
            panel = app.query_one("#activities", OptionList)
            self.assertEqual(panel.option_count, 12)
            self.assertEqual(len(app.store.get(app.current).agents), 12)
            await pilot.press(*(["down"] * 11), "enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Detail)
            self.assertIn("worker-11", [p["threadId"] for m, p in client.calls if m == "thread/read"])
            await pilot.press("escape")
            await client.events.put({"method": "turn/completed", "params": {
                "threadId": "worker-11", "turn": {"id": "turn-11", "status": "completed"}}})
            await pilot.pause(.2)
            self.assertEqual(app.store.get(app.current).agents["worker-11"]["status"], "completed")
            self.assertIn("Completed", panel.get_option("a-worker-11").prompt.plain)
