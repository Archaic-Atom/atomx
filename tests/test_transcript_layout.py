"""Transcript anchoring and flat Rich surfaces. 日志重排和无底色内容验证。"""
import io
import os
import tempfile
import unittest
from unittest.mock import patch

from rich.console import Console, Group

from arcatom_codex.appearance import PALETTES
from arcatom_codex.demo import DemoClient
from arcatom_codex.home_list import section_heading
from arcatom_codex.ui import ArcatomApp, Composer, pretty


class FlatContentTests(unittest.TestCase):
    def test_adjacent_file_changes_and_search_have_no_blank_rows(self):
        """Avoid a duplicate newline between compact tool summaries.

        文件修改和搜索摘要之间不留重复空行。
        """
        stream = io.StringIO()
        console = Console(file=stream, width=80, color_system=None)
        console.print(Group(
            pretty({"type": "fileChange", "changes": [{"path": "a.py"}]}),
            pretty({"type": "webSearch", "query": "AtomX"}),
            pretty({"type": "mcpToolCall", "server": "docs", "tool": "read"}),
        ))
        self.assertEqual(len(stream.getvalue().splitlines()), 3)

    def test_only_user_messages_have_gray_background(self):
        console = Console(width=80)
        for palette in PALETTES.values():
            heading = section_heading("Working", 2, palette.accent, palette, False)
            self.assertTrue(all(segment.style is None or segment.style.bgcolor is None
                                for segment in console.render(heading)))
            user = pretty({"type": "userMessage", "content": [
                {"type": "text", "text": "测试 message"}]}, palette=palette)
            text = next(segment for segment in console.render(user) if "测试" in segment.text)
            self.assertEqual(text.style.bgcolor.name, "#373737" if palette.dark else "#dcdcd9")
            self.assertTrue(text.style.bold)

    def test_inline_and_fenced_code_keep_colors_without_background(self):
        console = Console(width=80)
        for palette in PALETTES.values():
            for theme in ("monokai", "friendly"):
                for kind in ("agentMessage", "plan"):
                    message = pretty({"type": kind, "text":
                        "master: `d04a45d`\n\n```python\nprint('hello')\n```"},
                        code_theme=theme, palette=palette)
                    segments = list(console.render(message))
                    self.assertTrue(all(not segment.style or segment.style.bgcolor is None
                                        for segment in segments))
                    commit = next(segment for segment in segments if "d04a45d" in segment.text)
                    self.assertTrue(commit.style.bold)
                    self.assertIsNotNone(commit.style.color)
                    self.assertIn("print('hello')", "".join(segment.text for segment in segments))


class TranscriptLayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_and_file_change_toggle_inline_request_only(self) -> None:
        """Use the same clickable disclosure for other compact activities.

        搜索和文件修改同样可展开请求内容，但不把结果放进正文。
        """
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get("demo-dashboard")
            session.items.clear()
            session.ingest({
                "id": "search", "type": "webSearch",
                "query": "first query\nsecond query", "result": "HIDDEN_SEARCH_RESULT",
            })
            session.ingest({
                "id": "change", "type": "fileChange",
                "changes": [{"path": "a.py"}, {"path": "b.py"}],
                "output": "HIDDEN_DIFF",
            })
            session.ingest({
                "id": "tool", "type": "mcpToolCall", "server": "docs",
                "tool": "read", "arguments": {"path": "README.md"},
                "result": {"content": [{"type": "text", "text": "HIDDEN_TOOL_RESULT"}]},
            })
            app.paint(force=True)
            await pilot.pause(.1)
            transcript = app.query_one("#transcript")
            self.assertNotIn("second query", transcript.render().plain)
            for item_id, expected in (
                ("search", "second query"),
                ("change", "a.py\nb.py"),
                ("tool", '"path": "README.md"'),
            ):
                cell = next(
                    (x, y)
                    for y in range(app.size.height)
                    for x in range(app.size.width)
                    if app.screen.get_style_at(x, y).meta.get("atomx_activity_id")
                    == item_id
                )
                await pilot.click(offset=cell)
                await pilot.pause(.05)
                self.assertIn(expected, transcript.render().plain)
                self.assertNotIn("HIDDEN_", transcript.render().plain)
                cell = next(
                    (x, y)
                    for y in range(app.size.height)
                    for x in range(app.size.width)
                    if app.screen.get_style_at(x, y).meta.get("atomx_activity_id")
                    == item_id
                )
                await pilot.click(offset=cell)
                await pilot.pause(.3)
                self.assertNotIn(expected, transcript.render().plain)

    async def test_file_change_follows_text_without_blank_row(self):
        """Re-render a cached reply when a file-change item arrives.

        文件变更到达后更新已缓存的回复，避免两者之间出现空行。
        """
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get("demo-dashboard")
            session.items.clear()
            session.ingest({
                "id": "reply", "type": "agentMessage", "text": "Updated state.py."
            })
            app.paint(force=True)
            session.ingest({
                "id": "change", "type": "fileChange",
                "changes": [{"path": "state.py"}],
            })
            session.ingest({
                "id": "search", "type": "webSearch", "query": "AtomX"
            })
            app.paint(force=True)
            stream = io.StringIO()
            Console(file=stream, width=90, color_system=None).print(
                app.query_one("#transcript").content
            )
            lines = [line.rstrip() for line in stream.getvalue().splitlines()]
            message = lines.index("Updated state.py.")
            self.assertEqual(lines[message + 1], "  ◇ > Files changed · state.py")
            self.assertEqual(lines[message + 2], "  ⌕ > Search · AtomX")

    async def test_actual_screen_keeps_reply_background_clear_and_user_gray(self):
        with patch.dict(os.environ):
            os.environ.pop("NO_COLOR", None)
            app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.ingest({"id": "user", "type": "userMessage", "content": [
                {"type": "text", "text": "USER_GRAY_MARKER"}]})
            session.ingest({"id": "reply", "type": "agentMessage", "text":
                "REPLY_BODY **BOLD_REPLY** `d04a45d`\n\n```python\nprint('CODE_BODY')\n```"})
            for theme in PALETTES:
                app.apply_appearance({"ui_theme": theme})
                await pilot.pause(.1)
                update = app.screen._compositor.render_full_update()
                segments = [segment for line in update.strips for strip in line for segment in strip]
                for marker in ("REPLY_BODY", "BOLD_REPLY", "d04a45d", "CODE_BODY"):
                    matches = [segment for segment in segments if marker in segment.text]
                    self.assertTrue(matches, (theme, marker))
                    for segment in matches:
                        background = segment.style.bgcolor if segment.style else None
                        self.assertTrue(background is None or background.is_default,
                                        (theme, marker, background))
                user = next(segment for segment in segments if "USER_GRAY_MARKER" in segment.text)
                expected = "#dcdcd9" if theme == "paper" else "#373737"
                self.assertEqual(user.style.bgcolor.name, expected)
                bold = next(segment for segment in segments if "BOLD_REPLY" in segment.text)
                self.assertTrue(bold.style.bold)

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

    async def test_streamed_growth_tracks_new_bottom_and_preserves_browsing(self) -> None:
        """Follow each streamed layout without moving history browsing.

        流式增长逐次跟随新底部，浏览历史时保留位置。
        """
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(80, 28)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            scroll = app.query_one("#transcript-scroll")
            for count in (40, 55, 70, 85):
                session.ingest({"id": "stream", "type": "agentMessage",
                                "text": "\n\n".join(f"Line {i}" for i in range(count))})
                app.paint(force=True)
                await pilot.pause(.15)
                self.assertTrue(scroll.is_vertical_scroll_end,
                                (count, scroll.scroll_y, scroll.max_scroll_y))
            scroll.scroll_to(y=12, animate=False, immediate=True)
            await pilot.pause(.1)
            session.ingest({"id": "stream", "type": "agentMessage",
                            "text": "\n\n".join(f"Line {i}" for i in range(100))})
            app.paint(force=True)
            await pilot.pause(.15)
            self.assertEqual(scroll.scroll_y, 12)

    async def test_chat_headers_share_rows_and_keep_separator(self) -> None:
        """Keep status inline and a blank separator. 状态同行并保留空白分隔行。"""
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(130, 32)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            self.assertIn(app.connection_text, str(app.query_one("#brand").content))
            self.assertFalse(app.query_one("#connection").display)
            title = str(app.query_one("#chat-title").content)
            session = app.store.get(app.current)
            self.assertIn(session.title, title)
            self.assertIn(session.meta["cwd"], title)
            self.assertIn(session.status, title)
            self.assertEqual(str(app.query_one("#chat-path").content), "")
            self.assertEqual(app.query_one("#chat-path").size.height, 1)

    async def test_enter_send_follows_reply_through_waiting_and_composer_layouts(self) -> None:
        """Exercise actual submission and backend events, including old selections.

        真实按 Enter 发送并处理后端事件，覆盖旧选区、等待栏及输入框高度变化。
        """
        from textual.selection import Selection

        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(75, 26)) as pilot:
            await pilot.pause(.15)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"id": "history", "type": "agentMessage",
                            "text": "\n\n".join(f"History {i}" for i in range(60))})
            app.paint(force=True)
            await pilot.pause(.2)
            scroll = app.query_one("#transcript-scroll")
            composer = app.query_one(Composer)
            for prompt in ("test", "multi\nline\nquestion\nwith\na draft"):
                scroll.scroll_home(animate=False, immediate=True)
                app.screen.selections = {
                    app.query_one("#transcript"): Selection(None, None)
                }
                app.focus_composer(edit=True)
                composer.load_text(prompt)
                await pilot.press("enter")
                for _ in range(10):
                    await pilot.pause(.08)
                    self.assertTrue(scroll.is_vertical_scroll_end,
                                    (prompt, scroll.scroll_y, scroll.max_scroll_y))
                self.assertFalse(app.screen.selections)
            self.assertEqual(sum(m == "turn/start" for m, _ in app.client.calls), 2)

    async def test_layout_only_changes_keep_bottom_but_scroll_up_releases_it(self) -> None:
        """Anchor viewport changes without a new message. 无新消息的布局变化也保持贴底。"""
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(75, 26)) as pilot:
            await pilot.pause(.15)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"id": "history", "type": "agentMessage",
                            "text": "\n\n".join(f"History {i}" for i in range(60))})
            app.paint(force=True)
            await pilot.pause(.2)
            scroll = app.query_one("#transcript-scroll")
            for height in (22, 30, 24):
                await pilot.resize_terminal(75, height)
                await pilot.pause(.15)
                self.assertTrue(scroll.is_vertical_scroll_end)
            scroll.focus()
            scroll.scroll_to(y=12, animate=False, immediate=True)
            await pilot.pause(.1)
            await pilot.resize_terminal(75, 23)
            await pilot.pause(.15)
            self.assertEqual(scroll.scroll_y, 12)
            scroll.scroll_end(animate=False, immediate=True)
            await pilot.resize_terminal(75, 20)
            await pilot.pause(.15)
            self.assertTrue(scroll.is_vertical_scroll_end)
