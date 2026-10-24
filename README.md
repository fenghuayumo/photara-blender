# photara-blender

Blender 插件：把场景渲染成序列帧 + 导出每帧相机位姿，生成 Photara 可直接训练的
3DGS 数据集；渲染完成后可自动连接已打开的 **Photara Studio**（Agent Control），
或启动 Studio / CLI 开始 3D Gaussian Splatting 训练。

## 功能

- **逐帧渲染序列帧**（PNG / JPEG / WebP / TIFF / OpenEXR），模态执行，
  不冻结界面，可 ESC 取消，实时进度显示
- **导出每帧相机位姿**
  - `sparse/0/{cameras,images,points3D}.txt`：COLMAP 文本模型（3DGS 标准输入，
    Photara 外部数据集支持格式），w2c 四元数 + 平移，PINHOLE 内参
  - `transforms.json`：Nerfstudio / Blender 风格（`fl_x/fl_y/cx/cy/w/h/camera_model`
    + `transform_matrix`，c2w OpenGL，与 photara-studio
    `save_nerfstudio_transforms` 同一格式）
  - `photara_dataset.json`：数据集 manifest（内参、分辨率、逐帧位姿）
- **自动交付给 Photara**（渲染完成后）
  - **AUTO**：发现已打开的 Studio（Agent Control）→ 挂载数据集 → 自动开始训练；
    没有则自动启动 `photara_studio.exe --enable-agent-control` 并完成同样流程
  - **Connect Studio Only**：只连接已打开的 Studio
  - **CLI Training**：无 GUI，直接 `photara.exe --splat-dataset <dir> --output <name>.sog`
  - **Off**：只导出，不交付

## 安装

```powershell
# 打包成 Blender 可安装的 zip（顶层是 photara_render/ 目录）
powershell -File pack.ps1
```

Blender → Edit → Preferences → Add-ons → Install… 选择生成的
`photara_render-1.0.1.zip`，然后启用 **Photara Render & Pose Export**。

## 使用

1. 打开侧边栏：View3D → N 面板 → **Photara**
2. 设置：
   - **Output Root**：数据集输出目录（默认 `//photara_dataset`，即 blend 文件旁）
   - **Camera & Frames**：选择相机（留空用场景相机）、帧范围（支持场景范围/自定义/步长）
   - **Photara Integration**：交付模式与可执行文件路径（留空自动探测）
3. 点击 **Render & Export for Photara**。渲染完成后插件自动：
   - 读取 Agent Control 发现目录（`%APPDATA%\Photara\control\*.json`）
   - `POST /v1/rpc`：`studio.project.set_dataset`（外部数据集，跳过 SfM）
     → `studio.job.start {stage:"train"}`

> Studio 需要 `--enable-agent-control` 启动才会暴露控制接口；
> AUTO 模式由插件自动带上该参数。若 Studio 在运行但未开启控制接口，
> 插件会提示而不是盲目启动第二个实例。

## 输出目录结构

```text
photara_dataset/
├── images/
│   ├── frame_00000000.png
│   └── ...
├── sparse/0/                  # COLMAP 文本模型（3DGS 训练主格式）
│   ├── cameras.txt            # CAMERA_ID PINHOLE W H fx fy cx cy
│   ├── images.txt             # IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
│   └── points3D.txt           # 空（合成数据无需 SfM 点）
├── transforms.json            # Nerfstudio/Blender 位姿（fl_x 风格）
└── photara_dataset.json       # manifest
```

## 相机约定

Blender 相机：+X 右、+Y 上、看向 -Z（OpenGL）。
COLMAP/OpenCV 相机：+X 右、-Y 上、看向 +Z。

```text
R_w2c = diag(1,-1,-1) @ R_blender_c2w^T
t_w2c = -R_w2c @ C
```

内参（像素）：

```text
aspect = pixel_aspect_y / pixel_aspect_x
view_factor = width (HORIZONTAL) 或 aspect * height (VERTICAL)
fx = focal_mm / sensor_mm * view_factor
fy = fx / aspect
cx = width/2 - shift_x * view_factor
cy = height/2 + shift_y * view_factor / aspect
```

`transforms.json` 的 `transform_matrix` 保留 Blender `matrix_world` 的位置和旋转（移除相机缩放）
（c2w OpenGL），与 photara-studio 的 Nerfstudio 导出约定相同。

## 无头 / 脚本渲染

```powershell
blender -b scene.blend --python-expr "import photara_render.api as p; p.render_and_export(output_root='D:/data/scene01', camera_name='Camera', frame_start=1, frame_end=120, handover_mode='CLI')"
```

（要求 addon 已在 Blender 中启用；后台模式走阻塞执行路径。）

## 依赖的 Studio / CLI 能力（已在 photara-studio 源码验证）

| 能力 | 位置 |
|---|---|
| 外部数据集导入（COLMAP/RC/OpenMVS，导入相机跳过 SfM） | `photara/src/splat/dataset*.cpp` |
| Agent Control（发现文件 + Bearer token + `/v1/rpc`） | `apps/editor/control/` |
| `studio.project.set_dataset`（外部数据集挂载命令，本仓库配套新增） | `apps/editor/control/control_commands.cpp` |
| CLI 外部数据集 3DGS 训练（`--splat-dataset`） | `photara/src/tools/reconstruct.cpp` |
| Nerfstudio/Blender transforms.json 导出格式参考 | `photara/src/sfm/export_nerfstudio.cpp` |

## 测试

```powershell
python tests/test_camera_utils.py   # 相机数学往返、COLMAP/transforms.json 格式、参数解析
```

## 常见问题

- **必须是透视相机**：正交/全景相机不适用于 3DGS 重建，插件会直接报错。
- **透明背景**：3DGS 训练通常需要不透明背景，默认渲染期间临时关闭
  `film_transparent` 并在结束后恢复（可在 Output 面板关闭该行为）。
- **焦距动画**：内参逐帧求值；焦距、主点与分辨率按帧导出；COLMAP 为不同内参建立独立 camera，transforms.json 保存逐帧内参。
- **旧版 Studio**：没有 `studio.project.set_dataset` 时会回退为挂载图像目录并
  提示升级（升级后即可享受导入相机直训）。

## Nerfstudio 直接训练（本次更新）

新版 Photara 支持直接读取 `transforms.json`，也支持原有 COLMAP 导出。
在 Blender 面板勾选 `transforms.json` 即可；可以关闭 COLMAP，自动交付会明确选择 JSON，避免读取目录中残留的旧 COLMAP 文件。

```powershell
photara.exe --splat-dataset "D:/data/scene01/transforms.json" --output "D:/data/scene01/result.sog"
```

Studio 可通过外部数据集文件选择器打开 JSON，或由插件 AUTO / STUDIO 模式挂载并开始训练。
请使用本次源码编译的 Studio / CLI；旧版没有 Nerfstudio 导入器。
无点云时使用确定性随机初始化，也可在 Photara 指定初始 PLY。
训练静态 3DGS 时保持场景不动，只移动相机；推荐 RGB PNG、关闭 Render Region 和运动模糊。
相机自身或父级缩放不会写入位姿，世界坐标位置与方向保持不变。
内参包含 sensor fit、pixel aspect 和 lens shift；脚本指定帧范围后会自动关闭 Use Scene Range。
