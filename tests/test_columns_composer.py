"""Stable columns, marquee and multiline editing. 固定列、目录滚动和多行编辑。"""
import tempfile
import os
import time
import unittest
from unittest.mock import patch

from rich.cells import cell_len
from textual.document._document import Selection

from arcatom_codex.appearance import palette_for
from arcatom_codex.demo import DemoClient
from arcatom_codex.home_list import column_widths, directory_window, session_row
from arcatom_codex.state import Store
from arcatom_codex.ui import ArcatomApp, Composer


class ColumnTests(unittest.TestCase):
    def test_column_positions_do_not_depend_on_content_or_marquee(self):
        store = Store()
        for width in (73, 103, 153):
            sizes = column_widths(width)
            starts = [0, sizes[0] + 1, sum(sizes[:2]) + 2, sum(sizes[:3]) + 3]
            self.assertEqual(sum(sizes) + 3, width)
            for title, path in (("短标题", "/a"), ("Long title " * 15, "/very/long/目录/" * 30)):
                session = store.merge({"id": "row", "name": title, "preview": "SUMMARY",
                                       "cwd": path, "updatedAt": 1700000000})
                for step in (0, 15, 30):
                    row = session_row(session, width, palette_for({}), False, step).plain
                    self.assertEqual(cell_len(row), width)
                    self.assertEqual(cell_len(row[:row.index("SUMMARY")]), starts[1])
                    date = time.strftime("%m-%d %H:%M", time.localtime(1700000000))
                    self.assertEqual(cell_len(row[:row.index(date)]), starts[3])
            for size, ratio in zip(sizes, (.26, .30, .26, .18)):
                self.assertLess(abs(size - (width - 3) * ratio), 1.1)

    def test_unicode_marquee_reveals_full_directory_and_pauses_at_start(self):
        path = "/项目/一个很长的目录/with spaces/last-folder"
        windows = [directory_window(path, 12, step) for step in range(100)]
        self.assertEqual(windows[0], windows[3])
        self.assertTrue(any(window.endswith("last-folder") for window in windows))
        self.assertTrue(all(cell_len(window) <= 12 for window in windows))
        self.assertTrue(all(any(path[i:i+2] in window for window in windows)
                            for i in range(len(path) - 1)))

    def test_activity_timestamp_changes_for_work_not_history_read(self):
        store = Store()
        session = store.merge({"id": "t", "updatedAt": 1})
        store.merge({"id": "t", "turns": []}, history=True)
        self.assertEqual(session.meta["updatedAt"], 1)
        store.event("turn/completed", {"threadId": "t", "turn": {"id": "turn"}})
        self.assertGreater(session.meta["updatedAt"], 1)
        completed = session.meta["updatedAt"]
        store.merge({"id": "t", "updatedAt": 1})
        self.assertEqual(session.meta["updatedAt"], completed)


class ComposerLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_spacer_and_rows_recover_after_hidden_resize(self):
        with patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause(.2)
            options = app.query_one("#sessions")
            for width in (160, 60, 120):
                await app.open_session("demo-dashboard")
                await pilot.pause(.1)
                await pilot.resize_terminal(width, 32)
                app.paint_sessions()  # Simulate an update while home is hidden. 模拟后台更新。
                app.show_home()
                await pilot.pause(.2)
                spacer = app.query_one("#session-columns")
                self.assertEqual(spacer.region.height, 1)
                self.assertEqual(str(spacer.content), "")
                for index in options.selectable_indices():
                    option = options.get_option_at_index(index)
                    self.assertEqual(cell_len(option.prompt.plain), options.size.width - 3)
                    self.assertIn(app.store.get(option.id).title[:3], option.prompt.plain)
                self.assertTrue(options.has_focus)
                self.assertEqual(options.get_option_at_index(options.highlighted).id, "demo-dashboard")
            # Hovering an unselected row must not add an opaque strip. 悬停不涂黑条。
            index = options.get_option_index("demo-readme")
            y = options._index_to_line[index] - int(options.scroll_y)
            await pilot.hover("#sessions", offset=(3, y))
            await pilot.pause(.1)
            self.assertEqual(options._mouse_hovering_over, index)
            for segment in options.render_line(y):
                background = segment.style.bgcolor if segment.style else None
                self.assertTrue(background is None or background.is_default)

    async def test_newline_places_cursor_on_next_line_and_input_grows(self):
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(90, 32)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            await pilot.press("a", "shift+enter")
            self.assertEqual(composer.cursor_location, (1, 0))
            await pilot.press("b", "ctrl+j", "c", "shift+enter", "d", "shift+enter", "e")
            await pilot.pause(.2)
            self.assertEqual(composer.text, "a\nb\nc\nd\ne")
            self.assertEqual(composer.cursor_location, (4, 1))
            self.assertGreaterEqual(composer.content_size.height, 5)
            self.assertEqual(composer.scroll_y, 0)
            self.assertFalse(any(m == "turn/start" for m, _ in app.client.calls))
            composer.selection = Selection((0, 0), (1, 1))
            await pilot.press("shift+enter", "x")
            self.assertEqual(composer.text, "\nx\nc\nd\ne")
            composer.clear()
            await pilot.pause(.2)
            self.assertEqual(composer.region.height, 3)

    async def test_wrap_resize_and_large_draft_preserve_available_context(self):
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(100, 32)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            composer = app.query_one(Composer)
            draft = "中文自动换行 text " * 20
            composer.load_text(draft)
            composer.move_cursor(composer.document.end)
            await pilot.pause(.3)
            self.assertGreater(composer.size.height, 3)
            wide_height = composer.size.height
            await pilot.resize_terminal(60, 32)
            await pilot.pause(.3)
            self.assertGreaterEqual(composer.size.height, wide_height)
            self.assertEqual(composer.text, draft)
            composer.load_text("\n".join(f"Line {i}" for i in range(100)))
            composer.move_cursor(composer.document.end)
            await pilot.pause(.3)
            self.assertGreaterEqual(app.query_one("#transcript-scroll").size.height, 3)
            self.assertLessEqual(composer.region.bottom, app.query_one("#chat").region.bottom)
            self.assertGreater(composer.scroll_y, 0)

    async def test_marquee_keeps_focus_and_other_columns_still(self):
        client = DemoClient()
        client.threads["demo-login"]["cwd"] = "/long/path/" * 10 + "end"
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause(.2)
            options = app.query_one("#sessions")
            before = options.get_option("demo-login").prompt.plain
            other = options.get_option("demo-dashboard").prompt.plain
            app.marquee_step = 15
            app.advance_directory()
            after = options.get_option("demo-login").prompt.plain
            self.assertNotEqual(before, after)
            self.assertEqual(options.get_option("demo-dashboard").prompt.plain, other)
            self.assertTrue(options.has_focus)
            self.assertEqual(options.get_option_at_index(options.highlighted).id, "demo-login")
            self.assertEqual(before[-15:], after[-15:])
            await pilot.press("down")
            self.assertLessEqual(app.marquee_step, 3)
