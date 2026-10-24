"""Operators: modal frame-by-frame render + pose export, and utilities."""

import os
import re
import math

import bpy
from mathutils import Vector

from . import camera_utils, colmap_export, photara_bridge


FORMAT_EXT = {
    "PNG": ".png",
    "JPEG": ".jpg",
    "WEBP": ".webp",
    "TIFF": ".tif",
    "OPEN_EXR": ".exr",
}


class PhotaraExportError(Exception):
    pass


class RenderJob:
    """Prepare / step / finish job used by both the modal and the blocking path."""

    def __init__(self, context):
        self.props = context.scene.photara
        self.scene = context.scene
        self.camera = None
        self.frames = []
        self.records = []
        self.dataset_dir = ""
        self.images_dir = ""
        self.written = []
        self.width = 0
        self.height = 0
        self._prepared = False
        self._finished = False
        self._index = 0
        self._exported = False
        self._intr_min = None
        self._intr_max = None
        self._originals = {}
        self._original_frame = 0.0
        self._original_camera = None
        self._orbit_camera = None
        self._orbit_target_center = None
        self._orbit_radius = 0.0

    # ------------------------------------------------------------------ prepare
    def prepare(self):
        props = self.props
        scene = self.scene

        camera = props.camera or scene.camera
        if camera is None or camera.type != "CAMERA":
            raise PhotaraExportError("未找到相机：请设置场景相机或在面板中选择一个相机")
        self.camera = camera
        cam_type = camera.data.type
        if cam_type != "PERSP":
            raise PhotaraExportError(
                "相机必须是透视相机（当前：{}），正交/全景相机不适用于 3DGS 重建".format(cam_type)
            )

        if props.capture_mode == "ORBIT":
            target = props.orbit_target
            if target is None:
                raise PhotaraExportError("360° 环绕模式需要选择目标物体")
            if target == camera:
                raise PhotaraExportError("目标物体不能是拍摄相机")
            self.frames = list(range(1, int(props.orbit_shots) + 1))
        else:
            if props.use_scene_frame_range:
                start, end = scene.frame_start, scene.frame_end
            else:
                start, end = props.frame_start, props.frame_end
            if end < start:
                raise PhotaraExportError("帧范围无效：结束帧 {} 小于起始帧 {}".format(end, start))
            self.frames = list(range(int(start), int(end) + 1, max(1, int(props.frame_step))))
        if not self.frames:
            raise PhotaraExportError("帧范围为空")

        if not (props.export_colmap or props.export_transforms_json):
            raise PhotaraExportError("请至少启用 COLMAP 或 transforms.json 导出")
        if scene.render.use_border:
            raise PhotaraExportError("请关闭 Render Region 后导出完整图像")
        render = scene.render
        percentage = render.resolution_percentage / 100.0
        self.width = int(render.resolution_x * percentage)
        self.height = int(render.resolution_y * percentage)
        if self.width < 1 or self.height < 1:
            raise PhotaraExportError("渲染分辨率无效（{}x{}）".format(self.width, self.height))

        self.dataset_dir = os.path.abspath(bpy.path.abspath(props.output_root))
        self.images_dir = os.path.join(self.dataset_dir, "images")
        os.makedirs(self.images_dir, exist_ok=True)

        if props.overwrite:
            self._cleanup_old_frames()

        # ---- save & apply render settings -------------------------------------
        img = render.image_settings
        self._originals = {
            "filepath": render.filepath,
            "use_file_extension": render.use_file_extension,
            "film_transparent": render.film_transparent,
            "file_format": img.file_format,
            "color_mode": img.color_mode,
            "color_depth": img.color_depth,
            "quality": img.quality,
        }
        self._original_frame = float(scene.frame_current)
        self._original_camera = scene.camera
        if props.capture_mode == "ORBIT":
            self._prepare_orbit_camera(camera, props.orbit_target)

        fmt = props.image_format
        img.file_format = fmt
        img.color_mode = "RGB"
        img.color_depth = "32" if fmt == "OPEN_EXR" else "8"
        if fmt in ("JPEG", "WEBP"):
            img.quality = int(props.jpeg_quality)
        render.use_file_extension = False
        if props.force_opaque:
            render.film_transparent = False
        if props.capture_mode != "ORBIT" and props.camera and props.camera != scene.camera:
            scene.camera = props.camera

        self._prepared = True
        self._index = 0
        props.progress = 0.0
        props.status = "准备渲染：{} 帧 → {}".format(len(self.frames), self.dataset_dir)

    def _prepare_orbit_camera(self, source_camera, target):
        """Create a temporary, unanimated camera using the selected camera's optics."""
        deps = bpy.context.evaluated_depsgraph_get()
        target_eval = target.evaluated_get(deps)
        corners = [target_eval.matrix_world @ Vector(corner) for corner in target_eval.bound_box]
        if corners:
            center = sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)
            size = max(
                max(point[i] for point in corners) - min(point[i] for point in corners)
                for i in range(3)
            )
        else:
            center = target_eval.matrix_world.translation.copy()
            size = max(target_eval.dimensions)
        if size <= 1e-6:
            size = 1.0
        self._orbit_target_center = center
        self._orbit_radius = size * float(self.props.orbit_radius_factor)

        orbit_camera = source_camera.copy()
        orbit_camera.data = source_camera.data.copy()
        orbit_camera.name = "Photara Orbit Camera"
        orbit_camera.parent = None
        orbit_camera.animation_data_clear()
        for constraint in list(orbit_camera.constraints):
            orbit_camera.constraints.remove(constraint)
        self.scene.collection.objects.link(orbit_camera)
        orbit_camera.matrix_world = source_camera.matrix_world.copy()
        self._orbit_camera = orbit_camera
        self.camera = orbit_camera
        self.scene.camera = orbit_camera

    def _orbit_matrix(self, shot_index):
        props = self.props
        angle = float(props.orbit_start_angle) + 2.0 * math.pi * shot_index / len(self.frames)
        center = self._orbit_target_center
        position = Vector((
            center.x + self._orbit_radius * math.cos(angle),
            center.y + self._orbit_radius * math.sin(angle),
            center.z + float(props.orbit_height),
        ))
        rotation = (center - position).to_track_quat("-Z", "Y")
        matrix = rotation.to_matrix().to_4x4()
        matrix.translation = position
        return matrix

    def _cleanup_old_frames(self):
        """Remove only our own frame_XXXXXXXX files inside the images dir."""
        pattern = re.compile(r"^frame_\d+\.(png|jpg|jpeg|webp|tif|tiff|exr)$", re.IGNORECASE)
        try:
            entries = list(os.scandir(self.images_dir))
        except OSError:
            return
        for entry in entries:
            if entry.is_file(follow_symlinks=False) and pattern.match(entry.name):
                try:
                    os.remove(entry.path)
                except OSError:
                    pass

    # -------------------------------------------------------------------- step
    def step(self):
        """Render one frame. Returns True when everything (incl. exports) is done."""
        if not self._prepared:
            raise PhotaraExportError("任务尚未准备")
        if self._index >= len(self.frames):
            return self._finish_exports()

        props = self.props
        scene = self.scene
        frame = self.frames[self._index]
        if props.capture_mode == "ORBIT":
            self.camera.matrix_world = self._orbit_matrix(self._index)
            scene.frame_set(int(self._original_frame), subframe=self._original_frame - int(self._original_frame))
            bpy.context.view_layer.update()
        else:
            scene.frame_set(frame, subframe=0.0)

        deps = bpy.context.evaluated_depsgraph_get()
        cam_eval = self.camera.evaluated_get(deps)
        rigid_pose = cam_eval.matrix_world.to_quaternion().to_matrix().to_4x4()
        rigid_pose.translation = cam_eval.matrix_world.translation
        matrix = [list(row) for row in rigid_pose]  # copy to plain python
        intr = camera_utils.compute_intrinsics(
            cam_eval.data,
            self.width,
            self.height,
            scene.render.pixel_aspect_x,
            scene.render.pixel_aspect_y,
        )
        fx = intr["fx"]
        self._intr_min = fx if self._intr_min is None else min(self._intr_min, fx)
        self._intr_max = fx if self._intr_max is None else max(self._intr_max, fx)

        ext = FORMAT_EXT[props.image_format]
        filename = "frame_{:08d}{}".format(frame, ext)
        scene.render.filepath = os.path.join(self.images_dir, filename)
        bpy.ops.render.render(write_still=True)

        self.records.append(
            {
                "filename": filename,
                "matrix_world": matrix,
                "width": self.width,
                "height": self.height,
                "intrinsics": intr,
                "frame": frame,
            }
        )
        self._index += 1
        props.progress = self._index / float(len(self.frames))
        props.status = "渲染中 {}/{}（frame {}）".format(self._index, len(self.frames), frame)

        if self._index >= len(self.frames):
            return self._finish_exports()
        return False

    def _finish_exports(self):
        if self._exported:
            return True
        if not self.records:
            raise PhotaraExportError("没有可导出的帧")

        props = self.props
        self.written = colmap_export.write_dataset(
            self.dataset_dir,
            self.records,
            self.records[0]["intrinsics"],
            name=props.dataset_name,
            write_colmap=props.export_colmap,
            write_transforms=props.export_transforms_json,
            meta={
                "blender_version": bpy.app.version_string,
                "frame_range": (
                    [self.frames[0], self.frames[-1], 1]
                    if props.capture_mode == "ORBIT"
                    else [self.frames[0], self.frames[-1], props.frame_step]
                ),
                "image_format": props.image_format,
                "capture_mode": props.capture_mode,
                **({"orbit_target": props.orbit_target.name,
                    "orbit_source_frame": int(self._original_frame)}
                   if props.capture_mode == "ORBIT" else {}),
            },
        )
        self._exported = True
        msg = "导出完成：{} 帧 → {}".format(len(self.records), self.dataset_dir)

        props.status = msg
        return True

    # ------------------------------------------------------------------ finish
    def finish(self, cancelled=False, deliver=False, use_async=True):
        if self._finished or not self._prepared:
            return
        self._finished = True

        props = self.props
        render = self.scene.render
        img = render.image_settings
        o = self._originals
        try:
            render.filepath = o["filepath"]
            render.use_file_extension = o["use_file_extension"]
            render.film_transparent = o["film_transparent"]
            img.file_format = o["file_format"]
            img.color_mode = o["color_mode"]
            img.color_depth = o["color_depth"]
            img.quality = o["quality"]
            self.scene.camera = self._original_camera
            frame = self._original_frame
            self.scene.frame_set(int(frame), subframe=frame - int(frame))
        except Exception:
            pass

        if self._orbit_camera is not None:
            try:
                orbit_data = self._orbit_camera.data
                bpy.data.objects.remove(self._orbit_camera, do_unlink=True)
                if orbit_data.users == 0:
                    bpy.data.cameras.remove(orbit_data)
            except Exception:
                pass
            self._orbit_camera = None

        if cancelled or not deliver:
            return

        snapshot = photara_bridge.PropsSnapshot(props)

        if not use_async:
            result = photara_bridge.run_bridge(snapshot, self.dataset_dir)
            if result is not None:
                props.status = result.message
            return

        box = {"result": None}

        def on_done(result):
            box["result"] = result

        photara_bridge.submit_async(snapshot, self.dataset_dir, on_done)

        def poll():
            result = box["result"]
            if result is None:
                return 0.25
            try:
                props.status = result.message
            except Exception:
                pass
            return None

        try:
            bpy.app.timers.register(poll, first_interval=0.25)
        except Exception:
            pass


def _record_poses_blocking(context):
    """Evaluate per-frame poses without rendering (poses-only export)."""
    props = context.scene.photara
    scene = context.scene
    camera = props.camera or scene.camera
    if camera is None or camera.type != "CAMERA":
        raise PhotaraExportError("未找到相机：请设置场景相机或在面板中选择一个相机")
    if camera.data.type != "PERSP":
        raise PhotaraExportError("相机必须是透视相机")

    if props.use_scene_frame_range:
        start, end = scene.frame_start, scene.frame_end
    else:
        start, end = props.frame_start, props.frame_end
    frames = list(range(int(start), int(end) + 1, max(1, int(props.frame_step))))
    if not frames:
        raise PhotaraExportError("帧范围为空")

    render = scene.render
    percentage = render.resolution_percentage / 100.0
    width = int(render.resolution_x * percentage)
    height = int(render.resolution_y * percentage)
    dataset_dir = os.path.abspath(bpy.path.abspath(props.output_root))
    images_dir = os.path.join(dataset_dir, "images")

    ext = FORMAT_EXT[props.image_format]
    original_frame = float(scene.frame_current)
    records = []
    intr = None
    for frame in frames:
        scene.frame_set(frame, subframe=0.0)
        deps = context.evaluated_depsgraph_get()
        cam_eval = camera.evaluated_get(deps)
        rigid_pose = cam_eval.matrix_world.to_quaternion().to_matrix().to_4x4()
        rigid_pose.translation = cam_eval.matrix_world.translation
        records.append(
            {
                "filename": "frame_{:08d}{}".format(frame, ext),
                "matrix_world": [list(row) for row in rigid_pose],
                "width": width,
                "height": height,
            }
        )
        current_intrinsics = camera_utils.compute_intrinsics(
                cam_eval.data, width, height,
                render.pixel_aspect_x, render.pixel_aspect_y,
            )
        records[-1]["intrinsics"] = current_intrinsics
        if intr is None:
            intr = current_intrinsics
    frame = original_frame
    scene.frame_set(int(frame), subframe=frame - int(frame))

    missing = [
        r["filename"]
        for r in records
        if not os.path.isfile(os.path.join(images_dir, r["filename"]))
    ]
    os.makedirs(images_dir, exist_ok=True)
    colmap_export.write_dataset(
        dataset_dir,
        records,
        intr,
        name=props.dataset_name,
        write_colmap=props.export_colmap,
        write_transforms=props.export_transforms_json,
        meta={
            "blender_version": bpy.app.version_string,
            "frame_range": [frames[0], frames[-1], props.frame_step],
            "image_format": props.image_format,
        },
    )
    return records, dataset_dir, missing


class PHOTARA_OT_render_and_export(bpy.types.Operator):
    """渲染序列帧、导出每帧相机位姿（COLMAP/transforms.json），完成后交给 Photara"""

    bl_idname = "photara.render_and_export"
    bl_label = "Render & Export for Photara"
    bl_options = {"REGISTER", "UNDO"}

    _timer = None

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def invoke(self, context, event):
        if context.window is None or context.area is None:
            return self.execute(context)

        self.job = RenderJob(context)
        try:
            self.job.prepare()
        except PhotaraExportError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except OSError as e:
            self.report({"ERROR"}, "输出目录无法创建：{}".format(e))
            return {"CANCELLED"}

        self._timer = context.window_manager.event_timer_add(0.0, window=context.window)
        context.window_manager.modal_handler_add(self)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if event.type == "ESC":
            self.job.finish(cancelled=True)
            self._remove_timer(context)
            context.scene.photara.status = "已取消"
            self.report({"WARNING"}, "已取消（已渲染文件保留）")
            return {"CANCELLED"}

        if event.type == "TIMER":
            try:
                done = self.job.step()
            except PhotaraExportError as e:
                self.job.finish(cancelled=True)
                self._remove_timer(context)
                self.report({"ERROR"}, str(e))
                return {"CANCELLED"}
            except RuntimeError as e:
                self.job.finish(cancelled=True)
                self._remove_timer(context)
                self.report({"ERROR"}, "渲染失败：{}".format(e))
                return {"CANCELLED"}
            if done:
                self.job.finish(cancelled=False, deliver=True, use_async=True)
                self._remove_timer(context)
                self.report(
                    {"INFO"},
                    "完成：{} 帧 → {}".format(len(self.job.records), self.job.dataset_dir),
                )
                return {"FINISHED"}
        return {"PASS_THROUGH"}

    def execute(self, context):
        """Blocking path: EXEC_DEFAULT from UI scripts or background (blender -b)."""
        job = RenderJob(context)
        try:
            job.prepare()
            while not job.step():
                pass
        except PhotaraExportError as e:
            job.finish(cancelled=True)
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        except (RuntimeError, OSError) as e:
            job.finish(cancelled=True)
            self.report({"ERROR"}, "渲染/导出失败：{}".format(e))
            return {"CANCELLED"}
        job.finish(cancelled=False, deliver=True, use_async=False)
        self.report(
            {"INFO"}, "完成：{} 帧 → {}".format(len(job.records), job.dataset_dir)
        )
        return {"FINISHED"}

    def _remove_timer(self, context):
        if self._timer is not None:
            try:
                context.window_manager.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None


class PHOTARA_OT_export_poses(bpy.types.Operator):
    """不渲染，仅按当前设置导出相机位姿文件（图像应已存在）"""

    bl_idname = "photara.export_poses"
    bl_label = "Export Poses Only"
    bl_options = {"REGISTER"}

    @classmethod
    def poll(cls, context):
        return context.scene is not None

    def execute(self, context):
        try:
            records, dataset_dir, missing = _record_poses_blocking(context)
        except PhotaraExportError as e:
            self.report({"ERROR"}, str(e))
            return {"CANCELLED"}
        msg = "已导出 {} 个位姿 → {}".format(len(records), dataset_dir)
        if missing:
            msg += "（警告：{} 张图像缺失，请先渲染）".format(len(missing))
            self.report({"WARNING"}, msg)
        else:
            self.report({"INFO"}, msg)
        context.scene.photara.status = msg
        return {"FINISHED"}


class PHOTARA_OT_open_dataset_folder(bpy.types.Operator):
    """在系统文件管理器中打开数据集目录"""

    bl_idname = "photara.open_dataset_folder"
    bl_label = "Open Dataset Folder"
    bl_options = {"REGISTER"}

    def execute(self, context):
        path = os.path.abspath(bpy.path.abspath(context.scene.photara.output_root))
        if not os.path.isdir(path):
            self.report({"ERROR"}, "目录不存在：{}".format(path))
            return {"CANCELLED"}
        try:
            if os.name == "nt":
                os.startfile(path)  # noqa: S606
            elif os.uname().sysname == "Darwin":
                import subprocess

                subprocess.Popen(["open", path])
            else:
                import subprocess

                subprocess.Popen(["xdg-open", path])
        except OSError as e:
            self.report({"ERROR"}, "打开目录失败：{}".format(e))
            return {"CANCELLED"}
        return {"FINISHED"}


class PHOTARA_OT_test_studio_connection(bpy.types.Operator):
    """测试 Photara Studio Agent Control 连接，并探测本机可执行文件"""

    bl_idname = "photara.test_studio_connection"
    bl_label = "Test Connection"
    bl_options = {"REGISTER"}

    def execute(self, context):
        props = context.scene.photara
        messages = []

        endpoint = photara_bridge.discover_studio(timeout=float(props.studio_timeout))
        if endpoint is not None:
            messages.append(
                "Studio 已连接（{}，session {}）".format(
                    endpoint.get("url"), str(endpoint.get("session_id", "?"))[:8]
                )
            )
        elif photara_bridge.studio_process_running():
            messages.append("Studio 在运行但未开启 Agent Control（--enable-agent-control）")
        else:
            messages.append("未发现运行中的 Studio")

        studio_exe = photara_bridge.find_photara_executable("studio", props.studio_exe)
        cli_exe = photara_bridge.find_photara_executable("cli", props.cli_exe)
        messages.append(
            "Studio: {}".format(studio_exe if studio_exe else "未探测到（可手动指定）")
        )
        messages.append(
            "CLI: {}".format(cli_exe if cli_exe else "未探测到（可手动指定）")
        )

        text = "；".join(messages)
        props.status = text
        self.report({"INFO"}, text)
        return {"FINISHED"}
