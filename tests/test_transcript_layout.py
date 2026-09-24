"""Transcript anchoring and flat Rich surfaces. 日志重排和无底色内容验证。"""
import tempfile
import unittest

from rich.console import Console

from arcatom_codex.appearance import PALETTES
from arcatom_codex.demo import DemoClient
from arcatom_codex.home_list import section_heading
from arcatom_codex.ui import ArcatomApp, Composer, pretty


class FlatContentTests(unittest.TestCase):
    def test_headings_and_user_messages_have_no_panel_background(self):
        console = Console(width=80)
        for palette in PALETTES.values():
            for content in (
                    section_heading("Working", 2, palette.accent, palette, False),
                    pretty({"type": "userMessage", "content": [
                        {"type": "text", "text": "测试 message"}]}, palette=palette)):
                self.assertTrue(all(segment.style is None or segment.style.bgcolor is None
                                    for segment in console.render(content)))


class TranscriptLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_growing_and_shrinking_input_keeps_log_bottom_visible(self):
        client = DemoClient()
        client.threads["demo-dashboard"]["turns"] = [{
            "id": "history", "status": "completed", "items": [{
                "id": "log", "type": "userMessage", "content": [{
                    "type": "text", "text": "\n".join(
                        [f"Log line {i}" for i in range(80)] + ["LAST LOG LINE"])}]}]}]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(90, 32)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            scroll = app.query_one("#transcript-scroll")
            self.assertTrue(scroll.is_vertical_scroll_end)
            original_height = scroll.size.height
            original_gap = composer.region.y - scroll.region.bottom
            for draft in ("a\nb\nc\nd\ne\nf", "a\nb", ""):
                composer.load_text(draft)
                composer.move_cursor(composer.document.end)
                await pilot.pause(.2)
                self.assertTrue(scroll.is_vertical_scroll_end,
                                (draft, scroll.scroll_y, scroll.max_scroll_y))
                self.assertEqual(composer.region.y - scroll.region.bottom, original_gap)
                strips = app.screen._compositor.render_full_update().strips
                visible = "".join(segment.text for line in strips
                                  for strip in line for segment in strip)
                self.assertIn("LAST LOG LINE", visible)
            self.assertEqual(scroll.size.height, original_height)
            # Browsing stays at the same historical line when the draft changes.
            # 浏览旧记录或关闭自动跟随时，不把用户拉回底部。
            scroll.focus()
            scroll.scroll_to(y=20, animate=False)
            await pilot.pause(.1)
            composer.load_text("1\n2\n3\n4")
            await pilot.pause(.2)
            self.assertEqual(scroll.scroll_y, 20)
            app.view_preferences["follow_output"] = False
            app.focus_composer(edit=True)
            scroll.scroll_end(animate=False)
            await pilot.pause(.1)
            previous = scroll.scroll_y
            composer.load_text("1\n2\n3\n4\n5\n6")
            await pilot.pause(.2)
            self.assertEqual(scroll.scroll_y, previous)
