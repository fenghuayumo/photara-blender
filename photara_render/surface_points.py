"""Camera-mode-independent surface initialization from evaluated scene geometry.

Pixel-center rays find the first render-visible opaque surface. No SfM, random
3D positions or material base colors are used. All colors come from saved images.
"""
import json
import math
import os
import random
import shutil
from datetime import datetime

import numpy as np
from mathutils import Vector, Matrix

from .image_pixels import _rendered_pixels


def _render_hidden(scene, ignored):
    hidden = set(ignored)

    def visit(collection, parent_hidden=False):
        invisible = parent_hidden or collection.hide_render
        if invisible:
            hidden.update(collection.objects)
        for child in collection.children:
            visit(child, invisible)

    visit(scene.collection)
    hidden.update(obj for obj in scene.objects if obj.hide_render)
    return hidden


def _first_surface(scene, deps, origin, direction, distance, hidden):
    # Scene ray casting uses evaluated instances and modifiers, but does not
    # filter hide_render. Walk past such intersections, keeping true occluders.
    for _ in range(128):
        hit, position, normal, _, obj, _ = scene.ray_cast(
            deps, origin, direction, distance=distance)
        if not hit:
            return None
        if obj.original not in hidden:
            return position
        step = (position - origin).length + 1e-5
        distance -= step
        if distance <= 0:
            return None
        origin = position + direction * 1e-5
    return None


def build(context, records, images_dir, max_points=20000, samples_per_frame=1024,
          merge_distance=.01, ignored_objects=()):
    """Sample saved camera records and return (points, report).

    scene_frame identifies the evaluated geometry time, independently of image
    numbering. Callers must supply it (especially for orbit's fixed source frame).
    A voxel keeps its first actual surface position and averages saved RGB values.
    Reservoir sampling bounds the cloud without favoring early camera frames.
    """
    if max_points < 1 or samples_per_frame < 1 or merge_distance <= 0:
        raise ValueError("点云数量、每帧采样数和合并距离必须为正")
    scene = context.scene
    original_frame = scene.frame_current + scene.frame_subframe
    rng = random.Random(74621)
    cloud, slots, visited = {}, [], set()
    report = {"method": "visible_surface_pixel_rays", "frames": len(records),
              "sampled_rays": 0, "surface_hits": 0, "image_failures": [],
              "merge_distance": merge_distance, "max_points": max_points,
              "limitations": ["Opaque evaluated geometry; glass, mirrors, volumes, depth of field and motion blur are not simulated.",
                              "Use matching viewport/render geometry. Moving objects produce samples at their recorded scene time; static 3DGS cannot represent their motion."]}
    try:
        for frame in records:
            width, height = frame["width"], frame["height"]
            try:
                rgb, valid = _rendered_pixels(os.path.join(images_dir, frame["filename"]), width, height)
            except (RuntimeError, OSError, ValueError) as exc:
                report["image_failures"].append({"filename": frame["filename"], "error": str(exc)})
                continue
            time = frame["scene_frame"]
            scene.frame_set(math.floor(time), subframe=time - math.floor(time))
            deps = context.evaluated_depsgraph_get()
            hidden = _render_hidden(scene, ignored_objects)
            # Excluded/viewport-hidden render geometry cannot be ray-cast reliably.
            unavailable = [obj.name for obj in scene.objects if obj.type == "MESH"
                           and obj not in hidden and not obj.visible_get(view_layer=context.view_layer)]
            if unavailable:
                raise ValueError("请启用渲染几何的视口可见性后生成点云：" + ", ".join(unavailable[:5]))
            matrix = Matrix(frame["matrix_world"])
            rotation = matrix.to_3x3()
            intr = frame["intrinsics"]
            near, far = frame.get("clip_start", .1), frame.get("clip_end", 1000.)
            pixels = rng.sample(range(width * height), min(samples_per_frame, width * height))
            for pixel in pixels:
                x, y = pixel % width, pixel // width
                if not valid[height - 1 - y, x]:
                    continue
                local = Vector(((x + .5 - intr["cx"]) / intr["fx"],
                                (intr["cy"] - y - .5) / intr["fy"], -1))
                direction = rotation @ local.normalized()
                start, end = near * local.length, far * local.length
                origin = matrix.translation + direction * start
                report["sampled_rays"] += 1
                position = _first_surface(scene, deps, origin, direction, end - start, hidden)
                if position is None:
                    continue
                report["surface_hits"] += 1
                key = tuple(math.floor(v / merge_distance) for v in position)
                color = rgb[height - 1 - y, x].astype(float)
                if key in cloud:
                    cloud[key][1] += color
                    cloud[key][2] += 1
                    continue
                if key in visited:
                    continue
                visited.add(key)
                if len(slots) < max_points:
                    slots.append(key)
                else:
                    replace = rng.randrange(len(visited))
                    if replace >= max_points:
                        continue
                    del cloud[slots[replace]]
                    slots[replace] = key
                cloud[key] = [list(position), color, 1]
    finally:
        scene.frame_set(math.floor(original_frame), subframe=original_frame - math.floor(original_frame))
    points = [{"position": position, "color": np.rint(255 * color / count).astype(int).tolist()}
              for position, color, count in cloud.values()]
    report["unique_surface_voxels"] = len(visited)
    report["initial_surface_points"] = len(points)
    return points, report


def write_report(directory, report):
    with open(os.path.join(directory, "initial_points_report.json"), "w", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)


def rebuild_dataset(context, directory, **options):
    """Rebuild an existing export from its saved cameras, never current cameras.

    Images and camera files remain untouched; replace cloud files only after a
    successful nonempty build, backing up previous metadata/cloud files first.
    """
    from . import colmap_export
    path = os.path.join(directory, "transforms.json")
    with open(path, encoding="utf-8") as stream:
        transforms = json.load(stream)
    manifest_path = os.path.join(directory, "photara_dataset.json")
    with open(manifest_path, encoding="utf-8") as stream:
        manifest = json.load(stream)
    mode = manifest.get("capture_mode", "ANIMATION")
    start, _, step = manifest.get("frame_range", [1, 1, 1])
    fixed_time = manifest.get("orbit_source_frame", manifest.get("box_source_frame"))
    records = []
    for index, frame in enumerate(transforms["frames"]):
        time = frame.get("scene_frame", start + index * step if mode == "ANIMATION" else fixed_time)
        if time is None:
            raise ValueError("缺少采集时的场景帧，无法安全重建表面点云")
        relative = frame["file_path"]
        filepath = os.path.abspath(os.path.join(directory, relative))
        images_dir = os.path.abspath(os.path.join(directory, "images"))
        if os.path.commonpath([images_dir, filepath]) != images_dir:
            raise ValueError("图像路径必须位于数据集 images 目录")
        records.append({"filename": os.path.relpath(filepath, images_dir),
            "matrix_world": frame["transform_matrix"], "scene_frame": time,
            "width": frame.get("w", transforms["w"]), "height": frame.get("h", transforms["h"]),
            "intrinsics": {dst: frame.get(src, transforms[src])
                           for src, dst in (("fl_x", "fx"), ("fl_y", "fy"), ("cx", "cx"), ("cy", "cy"))},
            **{key: frame[key] for key in ("clip_start", "clip_end") if key in frame}})
    points, report = build(context, records, os.path.join(directory, "images"), **options)
    if not points or report["image_failures"]:
        raise ValueError("未生成有效点云或有图像读取失败；保留原数据，请检查场景、可见性和图片")
    files = ["transforms.json", "photara_dataset.json", "initial_points.ply",
             "sparse/0/points3D.txt", "initial_points_report.json"]
    backup = os.path.join(directory, "point_cloud_backups", datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    for relative in files:
        source = os.path.join(directory, relative)
        if os.path.isfile(source):
            destination = os.path.join(backup, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(source, destination)
    colmap_export.write_initial_cloud(directory, points, manifest.get("colmap", True))
    transforms["ply_file_path"] = "initial_points.ply"
    manifest["initial_point_cloud"] = report
    for filename, data in ((path, transforms), (manifest_path, manifest)):
        with open(filename, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
    write_report(directory, report)
    return points, report
