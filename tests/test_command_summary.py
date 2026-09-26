"""Collapsed command rendering and full detail access. 命令折叠及详情回归。"""
import io
import tempfile
import unittest

from rich.console import Console, Group
from textual.containers import VerticalScroll
from textual.widgets import OptionList

from arcatom_codex.demo import DemoClient
from arcatom_codex.dialogs import ActivityDetail
from arcatom_codex.rendering import COMMAND_SPINNER
from arcatom_codex.ui import ArcatomApp, Detail, pretty


class CommandSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_result_is_in_thread_detail_only(self) -> None:
        """Keep search output in Ctrl+T after adding inline disclosure.

        搜索正文只显示请求，结果在 Ctrl+T 线程详情中查看。
        """
        client = DemoClient()
        client.threads["demo-dashboard"]["turns"] = [{
            "id": "search-turn", "status": "completed", "items": [{
                "id": "search", "type": "webSearch", "query": "AtomX query",
                "result": "SEARCH_DETAIL_RESULT",
            }],
        }]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            self.assertNotIn(
                "SEARCH_DETAIL_RESULT", app.query_one("#transcript").render().plain
            )
            await pilot.press("ctrl+t", "enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, ActivityDetail)
            entries = app.screen.query_one("#activity-entries", OptionList)
            self.assertIn("Search", str(entries.get_option_at_index(0).prompt))
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Detail)
            self.assertIn(
                "SEARCH_DETAIL_RESULT",
                app.screen.query_one("#detail-body").render().plain,
            )

    async def test_thread_and_command_details_open_at_latest(self):
        """Focus the newest command and bottom of each detail.

        线程详情和命令输出均从最新位置打开。
        """
        client = DemoClient()
        client.threads["demo-dashboard"]["turns"] = [{
            "id": "long-turn", "status": "completed", "items": [
                {"id": "answer", "type": "agentMessage",
                 "text": "\n\n".join(f"Message {i}" for i in range(100))},
                *(
                    {"id": f"cmd-{i}", "type": "commandExecution",
                     "command": f"echo command-{i}", "status": "completed",
                     "aggregatedOutput": "\n".join(
                         f"output-{i}-{line}" for line in range(100)
                     )}
                    for i in range(3)
                ),
            ],
        }]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            await pilot.press("ctrl+t", "enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, ActivityDetail)
            scroll = app.screen.query_one("#detail-scroll", VerticalScroll)
            entries = app.screen.query_one("#activity-entries", OptionList)
            self.assertGreater(scroll.max_scroll_y, 0)
            self.assertTrue(scroll.is_vertical_scroll_end)
            self.assertEqual(entries.highlighted, 2)
            self.assertTrue(entries.has_focus)
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Detail)
            self.assertNotIsInstance(app.screen, ActivityDetail)
            self.assertIn("command-2", app.screen.query_one("#detail-body").render().plain)
            output_scroll = app.screen.query_one("#detail-scroll", VerticalScroll)
            self.assertGreater(output_scroll.max_scroll_y, 0)
            self.assertTrue(output_scroll.is_vertical_scroll_end)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, ActivityDetail)
            self.assertEqual(entries.highlighted, 2)

    async def test_large_script_stays_one_row_and_details_preserve_everything(self):
        command = '/bin/zsh -lc "python3 - <<\'PY\'\n' + 'print("SCRIPT_BODY")\n' * 300 + 'PY"'
        output = 'COMPLETE_OUTPUT\nline two'
        item = {"id": "script", "type": "commandExecution", "command": command,
                "status": "completed", "exitCode": 0, "aggregatedOutput": output}
        for status, mark in (("inProgress", COMMAND_SPINNER[0]),
                             ("completed", "✓"), ("failed", "!")):
            for width in (24, 80):
                stream = io.StringIO()
                console = Console(file=stream, width=width, color_system=None)
                console.print(Group(pretty({**item, "status": status})))
                lines = stream.getvalue().splitlines()
                self.assertEqual(len(lines), 1)
                self.assertIn(mark, lines[0])
                self.assertNotIn("SCRIPT_BODY", lines[0])
                self.assertNotIn("COMPLETE_OUTPUT", lines[0])
        client = DemoClient()
        client.threads["demo-dashboard"]["turns"] = [
            {"id": "script-turn", "status": "completed", "items": [item]}]
        app = ArcatomApp(tempfile.gettempdir(), client=client, demo=True)
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            self.assertFalse(app.query_one("#activities").display)
            body = app.query_one("#transcript").render().plain
            self.assertNotIn("SCRIPT_BODY", body)
            self.assertNotIn("COMPLETE_OUTPUT", body)
            await pilot.press("ctrl+t")
            panel = app.query_one("#activities", OptionList)
            self.assertTrue(panel.has_focus)
            self.assertNotIn("SCRIPT_BODY", panel.get_option_at_index(0).prompt.plain)
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, ActivityDetail)
            self.assertNotIn("SCRIPT_BODY", app.screen.query_one("#detail-body").render().plain)
            entries = app.screen.query_one("#activity-entries", OptionList)
            self.assertIn("/bin/zsh", str(entries.get_option_at_index(0).prompt))
            self.assertNotIn("SCRIPT_BODY", str(entries.get_option_at_index(0).prompt))
            await pilot.press("enter")
            await pilot.pause(.2)
            self.assertIsInstance(app.screen, Detail)
            detail = io.StringIO()
            Console(file=detail, width=120, color_system=None).print(
                app.screen.query_one("#detail-body").content
            )
            full = detail.getvalue()
            self.assertIn(command, full)
            self.assertIn(output, full)
            await pilot.press("escape")
            self.assertIsInstance(app.screen, ActivityDetail)
            await pilot.press("escape", "ctrl+t")
            self.assertFalse(app.query_one("#activities").display)

    async def test_inline_command_click_expands_and_animates_chevron(self) -> None:
        """Click the actual command cell to toggle its script only.

        点击真实命令单元格只展开命令，不显示过程输出，并验证箭头动画。
        """
        app = ArcatomApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(90, 30)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-dashboard")
            await pilot.pause(.2)
            session = app.store.get("demo-dashboard")
            session.items.clear()
            session.ingest({
                "id": "reply", "type": "agentMessage", "text": "Running check."
            })
            session.ingest({
                "id": "cmd-inline", "type": "commandExecution",
                "command": "printf first\nsecond", "status": "completed",
                "aggregatedOutput": "VISIBLE_OUTPUT", "exitCode": 0,
            })
            app.paint(force=True)
            await pilot.pause(.1)
            transcript = app.query_one("#transcript")
            body = transcript.render().plain
            self.assertIn("✓ > printf first", body)
            self.assertNotIn("second", body)
            self.assertNotIn("VISIBLE_OUTPUT", body)
            cell = next(
                (x, y)
                for y in range(app.size.height)
                for x in range(app.size.width)
                if app.screen.get_style_at(x, y).meta.get("atomx_activity_id")
                == "cmd-inline"
            )
            await pilot.click(offset=cell)
            await pilot.pause(.03)
            self.assertIn("second", transcript.render().plain)
            self.assertNotIn("VISIBLE_OUTPUT", transcript.render().plain)
            self.assertNotIn("Exit code", transcript.render().plain)
            self.assertIn("›", transcript.render().plain)
            session.ingest({
                "id": "cmd-inline", "type": "commandExecution",
                "command": "printf first\nsecond", "status": "completed",
                "aggregatedOutput": "VISIBLE_OUTPUT\nNEW_OUTPUT", "exitCode": 0,
            })
            app.paint(force=True)
            self.assertNotIn("NEW_OUTPUT", transcript.render().plain)
            await pilot.pause(.35)
            self.assertIn("▾", transcript.render().plain)
            cell = next(
                (x, y)
                for y in range(app.size.height)
                for x in range(app.size.width)
                if app.screen.get_style_at(x, y).meta.get("atomx_activity_id")
                == "cmd-inline"
            )
            await pilot.click(offset=cell)
            await pilot.pause(.03)
            self.assertNotIn("second", transcript.render().plain)
            await pilot.pause(.35)
            self.assertIn("✓ > printf first", transcript.render().plain)

    def test_running_spinner_changes_without_expanding(self) -> None:
        """Animate a pending command while keeping its body hidden.

        未结束命令只转动状态符号，不自动展开正文。
        """
        item = {"id": "pending", "type": "commandExecution",
                "command": "echo wait", "status": "inProgress",
                "aggregatedOutput": "SECRET_OUTPUT"}
        for frame in (0, 1):
            stream = io.StringIO()
            Console(file=stream, width=80, color_system=None).print(
                pretty(item, spinner_frame=frame)
            )
            self.assertIn(f"{COMMAND_SPINNER[frame]} > echo wait", stream.getvalue())
            self.assertNotIn("SECRET_OUTPUT", stream.getvalue())
