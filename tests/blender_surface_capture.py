"""End-to-end animation/orbit cloud export: run with Blender or Python+bpy."""
import json
from pathlib import Path
import sys
import tempfile

import bpy
import numpy as np
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import photara_render
from photara_render import capture_ops

photara_render.register()
bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
bpy.ops.mesh.primitive_cube_add(size=2)
target = bpy.context.object
bpy.ops.object.camera_add(location=(0, -6, 2))
camera = bpy.context.object
scene = bpy.context.scene
scene.camera = camera
scene.render.engine = "BLENDER_WORKBENCH"
scene.render.resolution_x = scene.render.resolution_y = 32
scene.render.resolution_percentage = 100
props = scene.photara
props.camera = camera
props.handover_mode = "OFF"
props.point_samples_per_frame = 1024
for frame, position in ((1, (0, -6, 2)), (2, (3, -5, 2))):
    camera.location = position
    camera.rotation_euler = (-Vector(position)).to_track_quat("-Z", "Y").to_euler()
    camera.keyframe_insert("location", frame=frame)
    camera.keyframe_insert("rotation_euler", frame=frame)
scene.frame_start, scene.frame_end = 1, 2

with tempfile.TemporaryDirectory(prefix="photara_surface_modes_") as directory:
    for mode in ("ANIMATION", "ORBIT"):
        props.capture_mode = mode
        props.orbit_target = target
        props.orbit_shots = 8
        props.orbit_levels = 3
        props.output_root = str(Path(directory, mode))
        scene.frame_set(7)
        job = capture_ops.RenderJob(bpy.context)
        job.prepare()
        while not job.step():
            pass
        job.finish()
        root = Path(props.output_root)
        report = json.loads((root / "initial_points_report.json").read_text())
        assert report["method"] == "visible_surface_pixel_rays" and report["initial_surface_points"] > 0
        assert not report["image_failures"]
        rows = [row.split() for row in (root / "sparse/0/points3D.txt").read_text().splitlines()
                if row and not row.startswith("#")]
        points = np.array([[float(v) for v in row[1:4]] for row in rows])
        # Every exported seed is on the actual cube surface, never its interior
        # or camera trajectory, for both capture modes.
        np.testing.assert_allclose(np.max(np.abs(points), axis=1), 1, atol=1e-5)
        transforms = json.loads((root / "transforms.json").read_text())
        assert transforms["ply_file_path"] == "initial_points.ply"
        expected_times = {7} if mode == "ORBIT" else {1, 2}
        assert {fr["scene_frame"] for fr in transforms["frames"]} == expected_times
        records, _, missing = capture_ops._record_poses_blocking(bpy.context)
        assert not missing and len(records) == len(job.records)
        assert json.loads((root / "initial_points_report.json").read_text())["initial_surface_points"] > 0
        print("SURFACE_MODE_OK", mode, len(points), flush=True)
print("SURFACE_CAPTURE_OK", flush=True)
