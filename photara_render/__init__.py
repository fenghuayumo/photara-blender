bl_info = {
    "name": "Photara Render & Pose Export",
    "author": "Photara Team",
    "version": (1, 0, 6),
    "blender": (3, 6, 0),
    "location": "View3D > Sidebar (N) > Photara",
    "description": "Render animation to image sequence, export per-frame camera poses "
    "(COLMAP / transforms.json), and hand the dataset to Photara Studio for 3DGS training",
    "doc_url": "",
    "category": "Render",
}

if "bpy" in locals():
    import importlib

    _modules = (
        "props",
        "camera_utils",
        "colmap_export",
        "photara_bridge",
        "render_ops",
        "ui",
    )
    for _m in _modules:
        importlib.reload(globals()[_m])
else:
    try:
        import bpy  # noqa: F401

        from . import props, camera_utils, colmap_export, photara_bridge, render_ops, ui
    except ImportError:
        # Outside Blender (unit tests): the pure modules still import fine.
        from . import camera_utils, colmap_export, photara_bridge


def _classes():
    return (
        props.PhotaraProps,
        render_ops.PHOTARA_OT_render_and_export,
        render_ops.PHOTARA_OT_cancel_render,
        render_ops.PHOTARA_OT_export_poses,
        render_ops.PHOTARA_OT_open_dataset_folder,
        render_ops.PHOTARA_OT_test_studio_connection,
        ui.PHOTARA_PT_main,
        ui.PHOTARA_PT_output,
        ui.PHOTARA_PT_frames,
        ui.PHOTARA_PT_photara,
    )


def register():
    if globals().get("props") is None:
        raise RuntimeError("photara_render 只能在 Blender 内启用")
    import bpy

    for cls in _classes():
        bpy.utils.register_class(cls)
    bpy.types.Scene.photara = bpy.props.PointerProperty(type=props.PhotaraProps)


def unregister():
    if globals().get("props") is None:
        raise RuntimeError("photara_render 只能在 Blender 内启用")
    import bpy

    del bpy.types.Scene.photara
    for cls in reversed(_classes()):
        bpy.utils.unregister_class(cls)
