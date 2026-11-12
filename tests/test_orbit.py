"""Orbit distribution and Blender pose checks: python -m unittest discover -s tests."""
import math
import unittest

import numpy as np

from photara_render.camera_utils import orbit_ring_layout


class OrbitDistributionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import photara_render
        photara_render.register()

    @classmethod
    def tearDownClass(cls):
        import photara_render
        photara_render.unregister()

    def test_direction_presets_and_custom_empty(self):
        import bpy
        import photara_render
        from photara_render.capture_ops import RenderJob, PhotaraExportError
        created = []
        try:
            bpy.ops.mesh.primitive_cube_add(size=2, location=(2, -3, 4))
            target = bpy.context.object
            created.append(target)
            props = bpy.context.scene.photara
            props.orbit_coverage = "UPPER_HEMISPHERE"
            props.orbit_direction = "POS_Z"
            job = RenderJob(bpy.context)
            base = np.array([np.array(p) for p in job._build_orbit_poses(target)])
            center = np.array(job._orbit_target_center)
            for direction, axis in (("POS_Z", (0, 0, 1)), ("NEG_Z", (0, 0, -1)),
                                    ("POS_X", (1, 0, 0)), ("NEG_X", (-1, 0, 0)),
                                    ("POS_Y", (0, 1, 0)), ("NEG_Y", (0, -1, 0))):
                props.orbit_direction = direction
                poses = np.array([np.array(p) for p in job._build_orbit_poses(target)])
                offsets = poses[:, :3, 3] - center
                self.assertTrue((offsets @ axis >= -1e-5).all())
                np.testing.assert_allclose(offsets[-1], np.array(axis) * job._orbit_radius, atol=1e-5)
                np.testing.assert_allclose(-poses[:, :3, 2], -offsets / job._orbit_radius, atol=1e-6)
                self.assertEqual(len(np.unique(np.round(offsets, 5), axis=0)), len(base))
            props.orbit_direction = "OBJECT"
            with self.assertRaises(PhotaraExportError):
                job._build_orbit_poses(target)
            parent = bpy.data.objects.new("Direction parent", None)
            control = bpy.data.objects.new("Direction control", None)
            for obj in (parent, control):
                bpy.context.scene.collection.objects.link(obj)
                created.append(obj)
            control.parent = parent
            parent.rotation_euler = (.2, -.6, .4)
            control.rotation_euler = (.5, .1, -.3)
            control.location = (50, 60, 70)
            control.scale = (2, 3, 4)
            props.orbit_orientation_object = control
            bpy.context.view_layer.update()
            poses = np.array([np.array(p) for p in job._build_orbit_poses(target)])
            rotation = np.array(control.matrix_world.to_quaternion().to_matrix())
            np.testing.assert_allclose(poses[:, :3, 3],
                                       (base[:, :3, 3] - center) @ rotation.T + center, atol=1e-5)
            for original, pose in zip(base, poses):
                np.testing.assert_allclose(pose[:3, :3], rotation @ original[:3, :3], atol=1e-6)
        finally:
            for obj in reversed(created):
                bpy.data.objects.remove(obj, do_unlink=True)

    def layout(self, shots=36, levels=4, coverage="UPPER_HEMISPHERE", low=-.5, high=1):
        return orbit_ring_layout(shots, levels, coverage, low, high)

    def test_hemisphere_preserves_budget_and_has_one_top(self):
        rings = self.layout()
        self.assertEqual(sum(count for _, count in rings), 36)
        self.assertEqual(rings[-1], (math.pi / 2, 1))
        self.assertEqual(rings[0][0], 0)
        self.assertGreater(rings[0][1], rings[1][1])
        self.assertGreater(rings[1][1], rings[2][1])

    def test_all_budgets_and_modes(self):
        for coverage in ("UPPER_HEMISPHERE", "FULL_SPHERE", "CUSTOM"):
            for levels in range(3, 13):
                for shots in (levels, 36, 71, 360):
                    rings = self.layout(shots, levels, coverage)
                    self.assertEqual(sum(count for _, count in rings), shots)
                    self.assertTrue(all(count >= 1 for _, count in rings))
                    for elevation, count in rings:
                        if abs(abs(elevation) - math.pi / 2) < 1e-8:
                            self.assertEqual(count, 1)
        sphere = self.layout(coverage="FULL_SPHERE")
        self.assertEqual(sphere[0], (-math.pi / 2, 1))
        self.assertEqual(sphere[-1], (math.pi / 2, 1))

    def test_single_ring_custom_endpoints_and_errors(self):
        self.assertEqual(self.layout(levels=1), [(math.pi / 4, 36)])
        self.assertEqual(self.layout(levels=1, coverage="CUSTOM", low=-1, high=-.3), [(-.3, 36)])
        self.assertEqual(self.layout(coverage="CUSTOM", low=0, high=float(np.float32(math.pi / 2)))[-1],
                         (math.pi / 2, 1))
        for args in ((3, 4, "CUSTOM", 0, 1), (36, 4, "CUSTOM", 1, 0),
                     (36, 2, "FULL_SPHERE", 0, 1), (36, 1, "CUSTOM", math.pi / 2, math.pi / 2)):
            with self.assertRaises(ValueError):
                orbit_ring_layout(*args)

    def test_blender_generated_positions_and_orientation(self):
        import bpy
        import photara_render
        from photara_render.capture_ops import RenderJob
        target = None
        try:
            bpy.ops.mesh.primitive_cube_add(size=2, location=(1, 2, 1))
            target = bpy.context.object
            props = bpy.context.scene.photara
            self.assertEqual(props.orbit_coverage, "UPPER_HEMISPHERE")
            self.assertEqual(props.orbit_levels, 4)
            for mode in ("UPPER_HEMISPHERE", "FULL_SPHERE", "CUSTOM"):
                props.orbit_coverage = mode
                job = RenderJob(bpy.context)
                poses = job._build_orbit_poses(target)
                center = np.array(job._orbit_target_center)
                positions = np.array([list(p.translation) for p in poses])
                self.assertEqual(len(poses), props.orbit_shots)
                self.assertEqual(len(np.unique(np.round(positions, 5), axis=0)), len(poses))
                np.testing.assert_allclose(np.linalg.norm(positions - center, axis=1),
                                           job._orbit_radius, atol=1e-5)
                for pose, position in zip(poses, positions):
                    rotation = np.array(pose.to_3x3())
                    np.testing.assert_allclose(-rotation[:, 2],
                                               (center - position) / np.linalg.norm(center - position), atol=1e-6)
                    self.assertAlmostEqual(np.linalg.det(rotation), 1, places=5)
                if mode == "UPPER_HEMISPHERE":
                    self.assertTrue((positions[:, 2] >= center[2]).all())
                    self.assertEqual(sum(np.linalg.norm(p[:2] - center[:2]) < 1e-5 for p in positions), 1)
        finally:
            if target is not None:
                bpy.data.objects.remove(target, do_unlink=True)
