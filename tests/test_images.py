"""Image protocol and actual transcript integration. 图片协议及真实会话布局验证。"""

import base64
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from textual.content import Content
from textual.selection import Selection

from arcatom_codex.demo import DemoClient
from arcatom_codex.images import ImageSource, load_image, message_images, original_image_path
from arcatom_codex.image_widgets import ImagePreview, MediaTranscript
from arcatom_codex.ui import AtomXApp
from arcatom_codex.widgets import Composer, SelectableTranscript


def png_data():
    stream = io.BytesIO()
    Image.new("RGB", (160, 80), "#e5a277").save(stream, format="PNG")
    return base64.b64encode(stream.getvalue()).decode()


class ImageSourceTests(unittest.TestCase):
    def test_all_backend_image_variants(self):
        data = "data:image/png;base64," + png_data()
        cases = [
            {"type": "userMessage", "content": [{"type": "localImage", "path": "test.png"}]},
            {"type": "userMessage", "content": [{"type": "image", "url": data}]},
            {"type": "imageView", "path": "test.png"},
            {"type": "imageGeneration", "savedPath": "test.png", "result": ""},
            {"type": "imageGeneration", "result": png_data()},
            {"type": "mcpToolCall", "result": {"content": [
                {"type": "image", "mimeType": "image/png", "data": png_data()}]}},
            {"type": "functionCallOutput", "output": [{"type": "input_image", "image_url": data}]},
            {"type": "dynamicToolCall", "contentItems": [{"type": "inputImage", "imageUrl": data}]},
        ]
        for item in cases:
            with self.subTest(kind=item["type"]):
                self.assertEqual(len(message_images(item, "/project")), 1)
        self.assertEqual(load_image(message_images(cases[-1], "/project")[0]).size, (160, 80))

    def test_markdown_relative_paths_reference_links_and_code_exclusion(self):
        sources = message_images({"type": "agentMessage", "text":
            "![图](<images/a b.png>)\n\n[原图](images/a%20b.png)\n\n"
            "![Reference][pic]\n\n[pic]: /tmp/ref.png\n\n"
            "```md\n![example](not-real.png)\n```"}, "/project")
        self.assertEqual([s.uri for s in sources], ["images/a%20b.png", "/tmp/ref.png"])
        self.assertEqual(sources[0].cwd, "/project")

    def test_local_data_missing_corrupt_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "image with space.png"
            Image.new("RGB", (50, 25), "red").save(path)
            for uri in [path.name, str(path), path.as_uri()]:
                self.assertEqual(load_image(ImageSource(uri, directory)).size, (50, 25))
                self.assertEqual(original_image_path(ImageSource(uri, directory), Path(directory)), path.resolve())
            with self.assertRaises(OSError):
                load_image(ImageSource("missing.png", directory))
            with self.assertRaises(ValueError):
                load_image(ImageSource("data:image/png;base64,***", directory))
            with patch("arcatom_codex.images.MAX_IMAGE_BYTES", 1):
                with self.assertRaises(ValueError):
                    load_image(ImageSource(str(path), directory))
        with self.assertRaises(ValueError):
            load_image(ImageSource("https://example.com/image.png", "/"))
        self.assertNotIn("base64", ImageSource("data:image/png;base64," + png_data(), "/").caption)


class ImageTranscriptTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_images_resize_reuse_copy_and_switch(self):
        app = AtomXApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(110, 44)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.4)
            session = app.store.get(app.current)
            session.items.clear()
            session.ingest({"type": "userMessage", "id": "prompt", "content": [
                {"type": "text", "text": "请看图片"},
                {"type": "image", "url": "data:image/png;base64," + png_data()}]})
            session.ingest({"type": "agentMessage", "id": "reply", "text": "Reply after image"})
            app.paint(force=True)
            await pilot.pause(.7)
            media = app.query_one(MediaTranscript)
            self.assertTrue(media.display)
            self.assertFalse(app.query_one("#transcript").display)
            card = media.query_one(ImagePreview)
            self.assertIsNotNone(card.decoded)
            self.assertGreater(card.pixels.region.width, 1)
            self.assertGreater(card.pixels.region.height, 1)
            self.assertLess(card.pixels.region.bottom, app.query_one(Composer).region.y)
            text_widgets = list(media.query(SelectableTranscript))
            self.assertTrue(all(isinstance(w.render(), Content) for w in text_widgets))
            reply = text_widgets[-1]
            app.screen.selections = {reply: Selection(None, None)}
            with patch.object(app, "copy_to_clipboard") as copy:
                app.action_copy_selection()
                self.assertIn("Reply after image", copy.call_args.args[0])
                self.assertNotIn("base64", copy.call_args.args[0])
            session.items["reply"]["text"] += " streamed"
            app.paint(force=True)
            await pilot.pause(.2)
            self.assertIs(media.query_one(ImagePreview), card)
            self.assertIn("streamed", media.query(SelectableTranscript).last().render().plain)
            await pilot.resize_terminal(60, 30)
            await pilot.pause(.3)
            self.assertLessEqual(card.pixels.region.right, 60)
            app.show_home()
            await pilot.pause(.2)
            self.assertFalse(card.is_on_screen)
            await app.open_session("demo-login")
            await pilot.pause(.3)
            self.assertTrue(media.display)
            other = next(s for s in app.store.sessions if s != "demo-login")
            await app.open_session(other)
            await pilot.pause(.5)
            self.assertFalse(media.display)
            self.assertEqual(len(media.query(ImagePreview)), 0)
            self.assertTrue(app.query_one("#transcript").display)

    async def test_tool_image_results_and_unreadable_image_do_not_disappear(self):
        app = AtomXApp(tempfile.gettempdir(), client=DemoClient(), demo=True)
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.pause(.2)
            await app.open_session("demo-login")
            await pilot.pause(.3)
            session = app.store.get(app.current)
            session.items.clear()
            app.store.event("item/completed", {"threadId": session.id, "item": {
                "type": "functionCallOutput", "id": "tool-image", "output": [
                    {"type": "input_image", "image_url": "data:image/png;base64," + png_data()}]}})
            session.ingest({"type": "imageView", "id": "missing", "path": "/missing/test.png"})
            app.paint(force=True)
            await pilot.pause(.5)
            cards = list(app.query_one(MediaTranscript).query(ImagePreview))
            self.assertEqual(len(cards), 2)
            self.assertIsNotNone(cards[0].decoded)
            self.assertIsNone(cards[1].decoded)
            self.assertIn("Cannot preview", str(cards[1].query_one(".image-caption").content))
            self.assertTrue(app.query_one(Composer).has_focus)
