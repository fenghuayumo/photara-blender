"""Run with Blender's Python/bpy: python tests/test_point_colors.py."""
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photara_render.image_pixels import _rendered_pixels


class PointColorTests(unittest.TestCase):
    def save_image(self, directory, name, pixels, fmt="PNG"):
        image = bpy.data.images.new(name, width=4, height=4, float_buffer=fmt == "OPEN_EXR")
        try:
            image.colorspace_settings.is_data = True
            image.pixels.foreach_set(np.asarray(pixels, dtype=np.float32).ravel())
            image.file_format = fmt
            image.filepath_raw = str(Path(directory, name))
            image.save()
        finally:
            bpy.data.images.remove(image)

    def test_saved_png_encoded_values_and_orientation(self):
        pixels = np.ones((4, 4, 4), dtype=np.float32)
        pixels[:, :, :3] = [1, 0, 0]
        pixels[2, 1, :3] = np.array([51, 102, 153]) / 255
        with tempfile.TemporaryDirectory() as directory:
            self.save_image(directory, "a.png", pixels)
            images_before = len(bpy.data.images)
            rgb, valid = _rendered_pixels(str(Path(directory, "a.png")), 4, 4)
            np.testing.assert_allclose(rgb[2, 1] * 255, [51, 102, 153], atol=1e-4)
            self.assertTrue(valid.all())
            self.assertEqual(len(bpy.data.images), images_before)

    def test_linear_exr_encoding_and_size_validation(self):
        pixels = np.ones((4, 4, 4), dtype=np.float32)
        pixels[:, :, :3] = [.0031308, .18, 2]
        with tempfile.TemporaryDirectory() as directory:
            self.save_image(directory, "linear.exr", pixels, "OPEN_EXR")
            rgb, valid = _rendered_pixels(str(Path(directory, "linear.exr")), 4, 4)
            np.testing.assert_allclose(rgb[0, 0], [.04044994, .46135613, 1], atol=1e-5)
            self.assertTrue(valid.all())
            images_before = len(bpy.data.images)
            with self.assertRaises(ValueError):
                _rendered_pixels(str(Path(directory, "linear.exr")), 8, 4)
            self.assertEqual(len(bpy.data.images), images_before)


if __name__ == "__main__":
    unittest.main()
