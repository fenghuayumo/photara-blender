import json
import os
from pathlib import Path
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
from photara_render import colmap_export
from photara_render.export_settings import selected_format, dataset_source


class FormatTests(unittest.TestCase):
    def fixtures(self):
        intr = {"fx": 50., "fy": 60., "cx": 32., "cy": 24.}
        second = np.eye(4)
        second[0, 3] = 1
        frames = [{"filename": "视图_{}.png".format(i), "width": 64, "height": 48,
                   "matrix_world": matrix.tolist(), "intrinsics": intr, "scene_frame": i + 1}
                  for i, matrix in enumerate((np.eye(4), second))]
        points = [{"position": [0., 0., -3.], "color": [12, 34, 56],
                   "observations": [{"image_index": 0, "x": 32., "y": 24.},
                                    {"image_index": 1, "x": 32-50/3, "y": 24.}]},
                  {"position": [0., 1., -3.], "color": [78, 90, 123]}]
        return frames, intr, points

    def test_selected_outputs_and_rebuild_records(self):
        frames, intr, points = self.fixtures()
        for fmt in ("COLMAP", "NERFSTUDIO", "PHOTARA", "BOTH"):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as directory:
                colmap_export.write_dataset(directory, frames, intr, initial_points=points, export_format=fmt)
                root = Path(directory)
                self.assertEqual((root/"sparse/0/cameras.txt").exists(), fmt in ("COLMAP", "BOTH"))
                self.assertEqual((root/"transforms.json").exists(), fmt in ("NERFSTUDIO", "BOTH"))
                self.assertEqual((root/"scene.asfm").exists(), fmt == "PHOTARA")
                self.assertTrue((root/"initial_points.ply").exists())
                manifest = json.loads((root/"photara_dataset.json").read_text())
                self.assertEqual(manifest["export_format"], fmt)
                self.assertEqual(manifest["capture_records"], frames)
                if fmt == "PHOTARA":
                    self.assertEqual(manifest["native_asfm"]["sparse_tracks"], 1)
                    self.assertEqual(manifest["native_asfm"]["single_view_points_in_ply_only"], 1)

    def test_native_binary_header_checksum_cameras_and_tracks(self):
        frames, intr, points = self.fixtures()
        with tempfile.TemporaryDirectory() as directory:
            colmap_export.write_dataset(directory, frames, intr, initial_points=points, export_format="PHOTARA", world_axes="BLENDER")
            data = (Path(directory)/"scene.asfm").read_bytes()
        self.assertEqual(data[:8], b"AETHSFM\0")
        version, minimum, length, expected = struct.unpack_from("<IIQQ", data, 8)
        self.assertEqual((version, minimum, length), (4, 1, len(data)-32))
        checksum = 14695981039346656037
        for value in data[32:]:
            checksum = (checksum ^ value) * 1099511628211 & 0xffffffffffffffff
        self.assertEqual(checksum, expected)
        offset = 32
        def count():
            nonlocal offset
            value, = struct.unpack_from("<Q", data, offset)
            offset += 8
            return value
        def record():
            nonlocal offset
            length = count()
            body = data[offset:offset+length]
            offset += length
            return body
        self.assertEqual(count(), 1)
        camera = struct.unpack("<III9dBI", record())
        self.assertEqual(camera[:7], (0, 64, 48, 50., 60., 32., 24.))
        self.assertEqual(count(), 2)
        for index in range(2):
            body = record()
            image_id, camera_id, path_size = struct.unpack_from("<IIQ", body)
            self.assertEqual((image_id, camera_id), (index, 0))
            self.assertEqual(body[16:16+path_size].decode(),
                             os.path.join(directory, "images", frames[index]["filename"]).replace("\\", "/"))
            pose = struct.unpack_from("<12d", body, 16+path_size)
            np.testing.assert_allclose(np.array(pose[:9]).reshape(3, 3), np.diag([1, -1, -1]))
            np.testing.assert_allclose(pose[9:], [index, 0, 0])
        self.assertEqual(count(), 1)
        track = record()
        self.assertEqual(struct.unpack_from("<3dQ", track), (0., 0., -3., 2))
        self.assertEqual(struct.unpack_from("<4I", track, 32), (0, 0, 1, 0))
        self.assertEqual(struct.unpack_from("<IB3B", track, 48), (2, 1, 12, 34, 56))
        self.assertEqual(offset, len(data))

    def test_native_paths_resolve_with_scene_or_images_base_and_legacy_repair(self):
        from photara_render import asfm_export
        frames, intr, points = self.fixtures()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            images = root / "images"
            images.mkdir()
            for frame in frames:
                (images / frame["filename"]).write_bytes(b"test image")
            path = root / "scene.asfm"
            original_string = asfm_export._string
            def legacy(value):
                if os.path.isabs(value):
                    value = "images/" + os.path.basename(value)
                return original_string(value)
            with patch.object(asfm_export, "_string", side_effect=legacy):
                asfm_export.write_asfm(str(path), frames, intr, points)
            old = path.read_bytes()
            report = asfm_export.repair_image_paths(path, images)
            self.assertEqual(report, {"images": 2, "changed_paths": 2})
            fixed = path.read_bytes()
            self.assertEqual(old[-64:], fixed[-64:])  # sparse track unchanged
            for frame in frames:
                absolute = str(images / frame["filename"]).replace("\\", "/")
                self.assertIn(absolute.encode(), fixed)
                self.assertTrue((root / absolute).is_file())
                self.assertTrue((images / absolute).is_file())
            self.assertEqual(asfm_export.repair_image_paths(path, images)["changed_paths"], 0)
            self.assertEqual(len(list(root.glob("*.before-path-fix-*"))), 1)

    def test_explicit_choice_beats_legacy_and_selects_fresh_source(self):
        props = SimpleNamespace(export_format="NERFSTUDIO", export_colmap=True)
        self.assertEqual(selected_format(props), "NERFSTUDIO")
        self.assertEqual(dataset_source(props, "D:/data"), os.path.join("D:/data", "transforms.json"))
        props.export_format = "PHOTARA"
        self.assertTrue(dataset_source(props, "D:/data").endswith("scene.asfm"))

    def test_native_world_rotation_preserves_tracks_and_roundtrips(self):
        from photara_render.asfm_export import rotate_world
        from photara_render.world_axes import BLENDER_TO_PHOTARA
        frames, intr, points = self.fixtures()
        with tempfile.TemporaryDirectory() as directory:
            colmap_export.write_dataset(directory, frames, intr, initial_points=points,
                                       export_format="PHOTARA", world_axes="BLENDER")
            path = Path(directory) / "scene.asfm"
            original = path.read_bytes()
            self.assertEqual(rotate_world(path, BLENDER_TO_PHOTARA), {"images": 2, "tracks": 1})
            rotate_world(path, BLENDER_TO_PHOTARA.T)
            self.assertEqual(path.read_bytes(), original)

    def test_rebuild_uses_manifest_for_colmap_native_and_json(self):
        from photara_render import surface_points
        frames, intr, points = self.fixtures()
        for fmt in ("COLMAP", "NERFSTUDIO", "PHOTARA"):
            with self.subTest(fmt=fmt), tempfile.TemporaryDirectory() as directory:
                colmap_export.write_dataset(directory, frames, intr, initial_points=points, export_format=fmt)
                root = Path(directory)
                camera = root / "sparse/0/cameras.txt"
                before = camera.read_bytes() if camera.exists() else None
                with patch.object(surface_points, "build", return_value=(points, {"image_failures": []})) as build, \
                     patch.object(surface_points, "add_observations"):
                    surface_points.rebuild_dataset(None, directory)
                    self.assertEqual(build.call_args.args[1], frames)
                self.assertEqual(camera.read_bytes() if camera.exists() else None, before)
                self.assertEqual((root / "transforms.json").exists(), fmt == "NERFSTUDIO")
                self.assertEqual((root / "scene.asfm").exists(), fmt == "PHOTARA")
