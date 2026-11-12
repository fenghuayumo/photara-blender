"""Run with Blender's Python/bpy: python tests/test_point_colors.py."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from photara_render.indoor_capture import CapturePlan, _rendered_pixels


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

    def plan_and_records(self):
        first = np.eye(4)
        second = np.eye(4)
        second[0, 3] = .25
        positions = [(0, 0, 0), (.25, 0, 0)]
        plan = CapturePlan(
            samples=[(-.5, .5, -2), (0, 0, 2), (0, 0, -2), (100, 0, -2)],
            observations=[positions, positions, [], positions],
            report={"intrinsics": {"fx": 2, "fy": 2, "cx": 2, "cy": 2},
                    "resolution": [4, 4], "clip_range": [.1, 10],
                    "required_distinct_positions": 2})
        records = [{"filename": "a.png", "matrix_world": first.tolist()},
                   {"filename": "b.png", "matrix_world": second.tolist(),
                    "intrinsics": {"fx": 4, "fy": 4, "cx": 2, "cy": 2},
                    "width": 4, "height": 4}]
        return plan, records

    def test_saved_pixel_projection_and_multiview_color(self):
        # Distinct upper/lower rows detect vertical flips and distinct columns
        # detect using the wrong intrinsics. These are encoded PNG values.
        pixels = np.ones((4, 4, 4), dtype=np.float32)
        pixels[:, :, :3] = [1, 0, 0]
        with tempfile.TemporaryDirectory() as directory:
            pixels[2, 1, :3] = np.array([51, 102, 153]) / 255
            self.save_image(directory, "a.png", pixels)
            pixels[2, 0, :3] = np.array([153, 204, 51]) / 255
            self.save_image(directory, "b.png", pixels)
            plan, records = self.plan_and_records()
            images_before = len(bpy.data.images)
            points = plan.initial_points(records, directory)
            self.assertEqual(points, [{"position": [-.5, .5, -2], "color": [102, 153, 102]}])
            self.assertEqual(plan.completed_counts, [2, 0, 0, 0])
            self.assertEqual(plan.report["initial_colored_points"], 1)
            self.assertEqual(plan.report["initial_gray_fallback_points"], 0)
            self.assertEqual(len(bpy.data.images), images_before)
            json.dumps(plan.report)

    def test_missing_images_fallback_and_duplicate_position(self):
        plan, records = self.plan_and_records()
        with tempfile.TemporaryDirectory() as directory:
            points = plan.initial_points(records, directory)
            self.assertEqual(points[0]["color"], [128, 128, 128])
            self.assertEqual(len(plan.report["initial_point_color_failures"]), 2)
            self.assertEqual(plan.report["initial_gray_fallback_points"], 1)
            self.assertEqual(plan.initial_points([records[0], records[0]], directory), [])

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
