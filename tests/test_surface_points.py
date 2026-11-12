import tempfile
from pathlib import Path
import unittest
import json

import bpy
import numpy as np

from photara_render import surface_points


class SurfacePointTests(unittest.TestCase):
    def setUp(self):
        self.scene = bpy.data.scenes.new("Surface cloud test")
        self.objects = []
        self.directory = tempfile.TemporaryDirectory()
        image = bpy.data.images.new("Surface pixels", width=8, height=8)
        pixels = np.ones((8, 8, 4), dtype=np.float32)
        for y in range(8):
            for x in range(8):
                pixels[y, x, :3] = [x / 7, y / 7, .2]
        image.colorspace_settings.is_data = True
        image.pixels.foreach_set(pixels.ravel())
        image.filepath_raw = str(Path(self.directory.name, "a.png"))
        image.file_format = "PNG"
        image.save()
        bpy.data.images.remove(image)

    def tearDown(self):
        for obj in self.objects:
            mesh = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
        bpy.data.scenes.remove(self.scene)
        self.directory.cleanup()

    def plane(self, z, hidden=False):
        mesh = bpy.data.meshes.new("Surface")
        mesh.from_pydata([(-10, -10, z), (10, -10, z), (10, 10, z), (-10, 10, z)], [], [(0, 1, 2, 3)])
        obj = bpy.data.objects.new("Surface", mesh)
        self.scene.collection.objects.link(obj)
        obj.hide_render = hidden
        self.objects.append(obj)
        return obj

    def record(self, time=1):
        return {"filename": "a.png", "matrix_world": np.eye(4).tolist(),
                "width": 8, "height": 8, "scene_frame": time,
                "intrinsics": {"fx": 8, "fy": 8, "cx": 4, "cy": 4},
                "clip_start": .1, "clip_end": 10}

    def build(self, records, **kwargs):
        with bpy.context.temp_override(scene=self.scene, view_layer=self.scene.view_layers[0]):
            return surface_points.build(bpy.context, records, self.directory.name,
                                        samples_per_frame=64, merge_distance=.001, **kwargs)

    def test_front_surface_occludes_back_and_pixel_projection(self):
        self.plane(-3)
        self.plane(-5)
        self.plane(-1, hidden=True)
        points, report = self.build([self.record()])
        self.assertEqual(len(points), 64)
        self.assertEqual(report["surface_hits"], 64)
        for point in points:
            x, y, z = point["position"]
            self.assertAlmostEqual(z, -3, places=5)
            u, v = int(8 * x / -z + 4), int(4 - 8 * y / -z)
            np.testing.assert_allclose(point["color"], [round(u * 255 / 7), round((7-v) * 255 / 7), 51], atol=1)

    def test_scene_time_budget_and_restoration(self):
        obj = self.plane(-3)
        obj.location.z = 0
        obj.keyframe_insert("location", frame=1)
        obj.location.z = -2
        obj.keyframe_insert("location", frame=2)
        self.scene.frame_set(7, subframe=.25)
        points, _ = self.build([self.record(1), self.record(2)])
        self.assertEqual({round(p["position"][2]) for p in points}, {-3, -5})
        self.assertEqual(self.scene.frame_current, 7)
        self.assertAlmostEqual(self.scene.frame_subframe, .25)
        limited, _ = self.build([self.record(1), self.record(2)], max_points=5)
        self.assertEqual(len(limited), 5)
        repeated, _ = self.build([self.record(1), self.record(2)], max_points=5)
        self.assertEqual(limited, repeated)

    def test_clipping_missing_images_and_viewport_exclusion(self):
        obj = self.plane(-3)
        frame = self.record()
        frame["clip_start"] = 4
        points, _ = self.build([frame])
        self.assertEqual(points, [])
        frame["filename"] = "missing.png"
        points, report = self.build([frame])
        self.assertEqual(points, [])
        self.assertEqual(len(report["image_failures"]), 1)
        obj.hide_viewport = True
        with self.assertRaisesRegex(ValueError, "视口"):
            self.build([self.record()])

    def test_rebuild_preserves_saved_cameras_and_backs_up_cloud(self):
        self.plane(-3)
        root = Path(self.directory.name)
        (root / "images").mkdir()
        (root / "a.png").replace(root / "images/a.png")
        frame = self.record()
        transforms = {"w": 8, "h": 8, "fl_x": 8, "fl_y": 8, "cx": 4, "cy": 4,
                      "frames": [{"file_path": "images/a.png", "transform_matrix": frame["matrix_world"],
                                  "scene_frame": 1}]}
        (root / "transforms.json").write_text(json.dumps(transforms))
        (root / "photara_dataset.json").write_text(json.dumps({"capture_mode": "ANIMATION", "colmap": True}))
        sparse = root / "sparse/0"
        sparse.mkdir(parents=True)
        (sparse / "cameras.txt").write_text("camera sentinel")
        (sparse / "images.txt").write_text("pose sentinel")
        (sparse / "points3D.txt").write_text("old cloud")
        with bpy.context.temp_override(scene=self.scene, view_layer=self.scene.view_layers[0]):
            points, _ = surface_points.rebuild_dataset(bpy.context, str(root), samples_per_frame=64)
        self.assertEqual(len(points), 64)
        self.assertEqual((sparse / "cameras.txt").read_text(), "camera sentinel")
        self.assertEqual((sparse / "images.txt").read_text(), "pose sentinel")
        backup = next((root / "point_cloud_backups").iterdir())
        self.assertEqual((backup / "sparse/0/points3D.txt").read_text(), "old cloud")
        updated = json.loads((root / "transforms.json").read_text())
        self.assertEqual(updated["frames"], transforms["frames"])
        self.assertEqual(updated["ply_file_path"], "initial_points.ply")
