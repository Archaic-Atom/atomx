"""Reply copy and gallery interactions. 回复复制及图片查看器交互。"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from arcatom_codex.demo import DemoClient
from arcatom_codex.image_widgets import ImageGallery, ImageLoader
from arcatom_codex.images import ImageSource
from arcatom_codex.response_actions import response_formats
from arcatom_codex.ui import AtomXApp, Composer


class ResponseFormatTests(unittest.TestCase):
    def test_plain_markdown_and_code_preserve_content(self) -> None:
        """Keep code exact and avoid soft wrapping prose. 代码精确且正文不随终端折行。"""
        source = "**Hello** " + "word " * 30 + "\n\n```python\ndef f():\n    return 'a  b'\n```"
        formats = {key: value for key, _, value in response_formats(source)}
        self.assertEqual(formats["markdown"], source)
        self.assertEqual(formats["code-1"], "def f():\n    return 'a  b'\n")
        self.assertIn("Hello " + ("word " * 30).rstrip(), formats["plain"])
        self.assertIn("def f():\n    return 'a  b'", formats["plain"])


class ResponseToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_f5_copies_selected_code_and_restores_draft(self) -> None:
        """Use the real picker sequence. 验证实际键盘选择流程。"""
        app = AtomXApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.3)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"id": "reply", "type": "agentMessage", "text": "Reply\n\n```python\ndef f():\n    return 1\n```"})
            app.query_one(Composer).load_text("keep draft")
            app.paint(force=True)
            with patch.object(app, "copy_to_clipboard") as copy:
                await pilot.press("f5", "enter")
                await pilot.pause(.1)
                await pilot.press("down", "down", "enter")
                await pilot.pause(.2)
                copy.assert_called_once_with("def f():\n    return 1\n")
            self.assertIs(app.screen, app.main_screen)
            self.assertEqual(app.query_one(Composer).text, "keep draft")

    async def test_gallery_zoom_switch_escape_and_updated_local_file(self) -> None:
        """Keep gallery navigation local and retain active work. 图片浏览不影响工作任务。"""
        with tempfile.TemporaryDirectory() as directory:
            paths = [Path(directory) / f"{i}.png" for i in range(2)]
            for path, color in zip(paths, ("red", "blue")):
                Image.new("RGB", (320, 160), color).save(path)
            loader = ImageLoader()
            source = ImageSource(str(paths[0]), directory)
            self.assertEqual((await loader.load(source)).getpixel((0, 0))[:3], (255, 0, 0))
            Image.new("RGB", (320, 160), "green").save(paths[0])
            self.assertEqual((await loader.load(source)).getpixel((0, 0))[:3], (0, 128, 0))
            app = AtomXApp(directory, client=DemoClient(), demo=True)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause(.2)
                await app.open_session("demo-login")
                await pilot.pause(.3)
                session = app.store.get(app.current)
                session.items.clear()
                for i, path in enumerate(paths):
                    session.ingest({"id": f"img-{i}", "type": "imageView", "path": str(path)})
                session.active_turn = "active-turn"
                app.query_one(Composer).load_text("unsent")
                app.paint(force=True)
                await pilot.press("f7")
                await pilot.pause(.3)
                self.assertIsInstance(app.screen, ImageGallery)
                gallery = app.screen
                self.assertEqual(gallery.index, 1)
                width = gallery.pixels.region.width
                await pilot.press("plus")
                await pilot.pause(.2)
                self.assertGreater(gallery.pixels.region.width, width)
                await pilot.press("left")
                await pilot.pause(.2)
                self.assertEqual(gallery.index, 0)
                self.assertEqual(gallery.decoded.getpixel((0, 0))[:3], (0, 128, 0))
                with patch("arcatom_codex.image_widgets.open_image_file") as opener:
                    await pilot.press("o")
                    await pilot.pause(.2)
                    opener.assert_called_once_with(paths[0].resolve())
                await pilot.press("right", "0", "escape")
                await pilot.pause(.2)
                self.assertIs(app.screen, app.main_screen)
                self.assertEqual(session.active_turn, "active-turn")
                self.assertEqual(app.query_one(Composer).text, "unsent")
                self.assertFalse(any(method == "turn/interrupt" for method, _ in app.client.calls))
