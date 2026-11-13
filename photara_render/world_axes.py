"""World-basis conversion, independent of camera optical-axis conventions."""
import numpy as np

BLENDER_TO_PHOTARA = np.array([[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]])


def resolve_axes(choice, export_format):
    if choice == "AUTO":
        return "BLENDER" if export_format in (None, "NERFSTUDIO") else "PHOTARA"
    if choice not in ("BLENDER", "PHOTARA"):
        raise ValueError("Unknown export world axes")
    return choice


def convert(frames, points, axes):
    rotation = BLENDER_TO_PHOTARA if axes == "PHOTARA" else np.eye(3)
    transform = np.eye(4)
    transform[:3, :3] = rotation
    converted_frames = [{**frame, "matrix_world":
                         (transform @ np.asarray(frame["matrix_world"], dtype=float)).tolist()}
                        for frame in frames]
    converted_points = [{**point, "position": (rotation @ point["position"]).tolist()}
                        for point in points]
    return converted_frames, converted_points
