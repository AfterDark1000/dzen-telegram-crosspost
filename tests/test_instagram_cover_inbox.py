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

    def test_generator_portrait_1092x1440_keeps_entire_art(self):
        from PIL import ImageDraw

        im = Image.new("RGB", (1092, 1440), (35, 65, 100))
        draw = ImageDraw.Draw(im)
        # Simulate text and important objects right against opposing edges.
        draw.rectangle((0, 0, 85, 85), fill=(255, 0, 0))
        draw.rectangle((1006, 1354, 1091, 1439), fill=(0, 255, 0))
        buff = io.BytesIO()
        im.save(buff, "PNG")

        data = inbox.to_jpeg(buff.getvalue())
        with Image.open(io.BytesIO(data)) as out:
            self.assertEqual(out.size, (1080, 1350))
            # ImageOps.contain yields 1024x1350; only 28px side fields.
            self.assertNotEqual(out.getpixel((0, 0)), out.getpixel((60, 40)))
            self.assertGreater(out.getpixel((60, 40))[0], 190)
            self.assertGreater(out.getpixel((1020, 1310))[1], 190)

    def test_three_by_four_portrait_is_letterboxed(self):
        im = Image.new("RGB", (900, 1200), (220, 80, 30))
        buff = io.BytesIO()
        im.save(buff, "PNG")
        data = inbox.to_jpeg(buff.getvalue())
        with Image.open(io.BytesIO(data)) as out:
            self.assertEqual(out.size, (1080, 1350))
            self.assertGreater(out.getpixel((540, 675))[0], 180)

    def test_extremely_narrow_portrait_is_rejected(self):
        im = Image.new("RGB", (700, 1400), "blue")
        buff = io.BytesIO()
        im.save(buff, "PNG")
        with self.assertRaisesRegex(ValueError, "Unsupported aspect ratio"):
            inbox.to_jpeg(buff.getvalue())

    def test_landscape_not_auto_cropped(self):
        im = Image.new("RGB", (1200, 675), "blue")
        buff = io.BytesIO()
        im.save(buff, "PNG")
        with self.assertRaisesRegex(ValueError, "aspect ratio"):
            inbox.to_jpeg(buff.getvalue())


if __name__ == "__main__":
    unittest.main()
