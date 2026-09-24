"""Collapsed command rendering and full detail access. 命令折叠及详情回归。"""
import io
import tempfile
import unittest

from rich.console import Console, Group
from textual.widgets import OptionList

from arcatom_codex.demo import DemoClient
from arcatom_codex.ui import ArcatomApp, Detail, pretty


class CommandSummaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_script_stays_one_row_and_details_preserve_everything(self):
        command = '/bin/zsh -lc "python3 - <<\'PY\'\n' + 'print("SCRIPT_BODY")\n' * 300 + 'PY"'
        output = 'COMPLETE_OUTPUT\nline two'
        item = {"id": "script", "type": "commandExecution", "command": command,
                "status": "completed", "exitCode": 0, "aggregatedOutput": output}
        for status, mark in (("inProgress", "●"), ("completed", "✓"), ("failed", "!")):
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
            self.assertIsInstance(app.screen, Detail)
            full = app.screen.query_one("#detail-body").content.plain
            self.assertIn(command, full)
            self.assertIn(output, full)
            await pilot.press("escape", "ctrl+t")
            self.assertFalse(app.query_one("#activities").display)
