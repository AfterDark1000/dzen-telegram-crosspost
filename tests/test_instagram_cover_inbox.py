"""Offline safety checks for photo inbox (no credentials or network)."""
import io
from pathlib import Path
import sys
import unittest
from PIL import Image
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import instagram_cover_inbox as inbox


class InboxTests(unittest.TestCase):
    def test_normalized_title(self):
        self.assertEqual(inbox.normalize_title("  GPT — новая   модель "),
                         inbox.normalize_title("gpt - НОВАЯ модель"))

    def test_portrait_image_accepted(self):
        im = Image.new("RGB", (800, 1000), "red")
        buff = io.BytesIO()
        im.save(buff, "PNG")
        data = inbox.to_jpeg(buff.getvalue())
        with Image.open(io.BytesIO(data)) as out:
            self.assertEqual(out.size, (1080, 1350))
            self.assertEqual(out.format, "JPEG")

    def test_landscape_not_auto_cropped(self):
        im = Image.new("RGB", (1200, 675), "blue")
        buff = io.BytesIO()
        im.save(buff, "PNG")
        with self.assertRaisesRegex(ValueError, "aspect ratio"):
            inbox.to_jpeg(buff.getvalue())


if __name__ == "__main__":
    unittest.main()
