import bpy

from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)


def _poll_camera(self, obj):
    return obj is not None and obj.type == "CAMERA"


def _poll_mesh_or_object(self, obj):
    return obj is not None and obj.type in {"MESH", "CURVE", "SURFACE", "META", "FONT"}


class PhotaraProps(bpy.types.PropertyGroup):
    # ------------------------------------------------------------------ output
    output_root: StringProperty(
        name="Output Root",
        description="数据集输出根目录（包含 images/ 与 sparse/0/）",
        subtype="DIR_PATH",
        default="//photara_dataset",
    )
    dataset_name: StringProperty(
        name="Dataset Name",
        description="数据集名称，会写入 manifest 并提交给 Photara Studio",
        default="blender_scene",
    )
    image_format: EnumProperty(
        name="Image Format",
        description="序列帧格式。3DGS 训练推荐 PNG 或 JPEG（8bit 已 tone-map 的图像）",
        items=(
            ("PNG", "PNG", "无损 8bit，最稳妥的选择", "IMAGE_DATA", 0),
            ("JPEG", "JPEG", "体积小，可设置质量", "IMAGE_DATA", 1),
            ("WEBP", "WebP", "体积小", "IMAGE_DATA", 2),
            ("TIFF", "TIFF", "无损（体积大）", "IMAGE_DATA", 3),
            ("OPEN_EXR", "OpenEXR", "HDR 线性数据（部分 3DGS 流程支持）", "IMAGE_DATA", 4),
        ),
        default="PNG",
    )
    jpeg_quality: IntProperty(
        name="Quality",
        description="JPEG/WebP 质量",
        default=92,
        min=1,
        max=100,
    )
    force_opaque: BoolProperty(
        name="Force Opaque Background",
        description="渲染期间临时关闭透明背景。3DGS 训练通常需要不透明背景",
        default=True,
    )
    overwrite: BoolProperty(
        name="Overwrite Existing Frames",
        description="开始前清理输出目录中旧的 frame_XXXX 文件",
        default=True,
    )

    # ------------------------------------------------------------------ frames
    use_scene_frame_range: BoolProperty(
        name="Use Scene Range",
        description="使用场景的播放范围，否则使用下方自定义范围",
        default=True,
    )
    frame_start: IntProperty(name="Start", default=1, soft_min=0)
    frame_end: IntProperty(name="End", default=120, soft_min=1)
    frame_step: IntProperty(
        name="Step",
        description="帧间隔（隔帧采样可减少训练数据量）",
        default=1,
        min=1,
    )
    camera: PointerProperty(
        name="Camera",
        description="用于渲染与导出位姿的相机（留空则使用场景相机）",
        type=bpy.types.Object,
        poll=_poll_camera,
    )
    capture_mode: EnumProperty(
        name="Capture Mode",
        description="使用已有相机动画，或自动生成物体 360° 环绕点位",
        items=(
            ("ANIMATION", "Camera Animation", "按场景帧范围渲染已有相机动画", 0),
            ("ORBIT", "Object 360° Orbit", "围绕目标物体自动生成相机点位", 1),
        ),
        default="ANIMATION",
    )
    orbit_target: PointerProperty(
        name="Target Object",
        description="环绕拍摄的目标物体；自动以其世界空间边界框中心为注视点",
        type=bpy.types.Object,
        poll=_poll_mesh_or_object,
    )
    orbit_shots: IntProperty(
        name="Shots",
        description="环绕一周的拍摄张数（不重复拍摄起点）",
        default=36,
        min=4,
        max=360,
    )
    orbit_radius_factor: FloatProperty(
        name="Radius × Object Size",
        description="环绕半径相对于目标物体世界空间最大尺寸的倍数",
        default=3.0,
        min=0.25,
        max=20.0,
    )
    orbit_height: FloatProperty(
        name="Height Offset",
        description="相机轨道相对于目标中心的垂直偏移（Blender 世界单位）",
        default=0.0,
        min=-1000.0,
        max=1000.0,
        subtype="DISTANCE",
    )
    orbit_start_angle: FloatProperty(
        name="Start Angle",
        description="环绕起始角度",
        default=0.0,
        min=-6.28318530718,
        max=6.28318530718,
        subtype="ANGLE",
    )

    # ------------------------------------------------------------------ export
    export_colmap: BoolProperty(
        name="COLMAP (3DGS)",
        description="导出 COLMAP 文本模型 sparse/0/{cameras,images,points3D}.txt —— 3DGS 标准输入",
        default=True,
    )
    export_transforms_json: BoolProperty(
        name="transforms.json",
        description="额外导出 nerfstudio 风格 transforms.json（OpenGL 相机约定）",
        default=True,
    )

    # ------------------------------------------------------------------ bridge
    handover_mode: EnumProperty(
        name="After Render",
        description="渲染导出完成后如何把数据集交给 Photara",
        items=(
            (
                "AUTO",
                "Auto",
                "连接已打开的 Photara Studio（需 --enable-agent-control）；"
                "未发现则自动启动 Studio 并挂载数据集",
                0,
            ),
            (
                "STUDIO",
                "Connect Studio Only",
                "仅连接已打开的 Studio（挂载数据集并开始训练），不启动新实例",
                1,
            ),
            (
                "CLI",
                "CLI Training",
                "无 GUI：直接用 photara.exe --splat-dataset 启动 3DGS 训练",
                2,
            ),
            ("OFF", "Off", "渲染后不做任何交付", 3),
        ),
        default="AUTO",
    )
    studio_timeout: FloatProperty(
        name="Timeout",
        description="连接 Studio Agent Control 的超时时间（秒）",
        default=1.5,
        min=0.1,
        max=30.0,
    )
    studio_exe: StringProperty(
        name="Studio Executable",
        description="photara_studio.exe 路径。留空则自动探测 PATH 与常见安装目录",
        subtype="FILE_PATH",
        default="",
    )
    studio_args: StringProperty(
        name="Studio Arguments",
        description=(
            "启动 Studio 的参数模板；需要包含 --enable-agent-control 才能被插件连接，"
            "{dataset} 会替换为数据集目录"
        ),
        default="--enable-agent-control",
    )
    launch_wait: FloatProperty(
        name="Launch Wait",
        description="启动 Studio 后等待其控制服务就绪的时间（秒）",
        default=20.0,
        min=1.0,
        max=120.0,
    )
    cli_exe: StringProperty(
        name="CLI Executable",
        description="photara.exe（reconstruct CLI）路径。留空则自动探测",
        subtype="FILE_PATH",
        default="",
    )
    cli_args: StringProperty(
        name="CLI Arguments",
        description=(
            "CLI 训练参数模板；{dataset} 替换为数据集目录，{name} 替换为数据集名。"
            "外部数据集不加 --dense 时即执行 3DGS 训练"
        ),
        default='--splat-dataset "{dataset}" --output "{dataset}/{name}.sog"',
    )
    auto_start_training: BoolProperty(
        name="Auto Start 3DGS Training",
        description="挂载数据集后立即通过 studio.job.start 开始 3DGS 训练",
        default=True,
    )

    # ------------------------------------------------------------------ status
    status: StringProperty(name="Status", default="就绪")
    progress: FloatProperty(
        name="Progress", default=0.0, min=0.0, max=1.0, subtype="PERCENTAGE"
    )
