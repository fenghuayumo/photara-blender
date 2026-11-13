import unittest
import numpy as np
from photara_render import camera_utils, world_axes


class WorldAxesTests(unittest.TestCase):
    def test_up_handedness_and_projection_invariance(self):
        q = world_axes.BLENDER_TO_PHOTARA
        np.testing.assert_allclose(q @ [0, 0, 1], [0, -1, 0])
        self.assertAlmostEqual(np.linalg.det(q), 1)
        m = np.eye(4)
        m[:3, :3] = camera_utils.matrix_from_quaternion([.8, .2, .3, .4])
        m[:3, 3] = [2, 3, 4]
        point = {"position": [1., 2., -3.], "color": [10, 20, 30]}
        frames, points = world_axes.convert([{"matrix_world": m.tolist()}], [point], "PHOTARA")
        transformed = np.array(frames[0]["matrix_world"])
        before = m[:3, :3].T @ (np.array(point["position"]) - m[:3, 3])
        after = transformed[:3, :3].T @ (np.array(points[0]["position"]) - transformed[:3, 3])
        np.testing.assert_allclose(after, before, atol=1e-12)
        self.assertEqual(points[0]["color"], point["color"])
        self.assertEqual(world_axes.resolve_axes("AUTO", "PHOTARA"), "PHOTARA")
        self.assertEqual(world_axes.resolve_axes("AUTO", "NERFSTUDIO"), "BLENDER")
