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
`photara_render-1.1.0.zip`，然后启用 **Photara Render & Pose Export**。

## 使用

1. 打开侧边栏：View3D → N 面板 → **Photara**
2. 设置：
   - **Output Root**：数据集输出目录（默认 `//photara_dataset`，即 blend 文件旁）
   - **Dataset Format**：选择下表中的相机与点云格式；与动画、环绕、室内点阵采集模式无关
   - **Camera & Frames**：选择相机（留空用场景相机）和采集模式
     - **Camera Animation**：按场景范围或自定义帧范围采样已有相机动画
     - **Object 360° Orbit**：选择目标物体，设置总张数、垂直环绕层数、最低/最高仰角、物体尺寸倍数定义的球面半径和起始角；插件将张数分配到各仰角层，自动生成球面透视相机点位
     - **Indoor Box Grid**：选择室内采集边界盒，默认启用 Automatic Coverage；生成多高度、上下全方向且有角度重叠的透视图，排除贴近几何和封闭家具内部的点位，并针对采样覆盖不足的表面自动补拍
   - **Photara Integration**：交付模式与可执行文件路径（留空自动探测）
3. 点击 **Render & Export for Photara**。渲染完成后插件自动：
   - 读取 Agent Control 发现目录（`%APPDATA%\Photara\control\*.json`）
   - `POST /v1/rpc`：`studio.project.set_dataset`（外部数据集，跳过 SfM）
     → `studio.job.start {stage:"train"}`

> Studio 需要 `--enable-agent-control` 启动才会暴露控制接口；
> AUTO 模式由插件自动带上该参数。若 Studio 在运行但未开启控制接口，
> 插件会提示而不是盲目启动第二个实例。

### 导出格式

World Axes 控制场景世界坐标，与相机的 OpenGL/OpenCV 光轴约定分开处理。Automatic 默认让 COLMAP、
Photara 原生和双格式输出匹配 Studio 的 **-Y 向上**；Nerfstudio 默认保留 Blender **+Z 向上**。
也可显式选择 Photara (-Y Up) 或 Blender (+Z Up)。Photara 转换为右手旋转 `(x,y,z) → (x,-z,y)`，
同时作用于点云位置、相机位置和朝向，保持原图投影不变，不进行单轴镜像。采样和 capture_records
始终保留 Blender 原坐标；从已保存相机重建点云时，再按 manifest 中保存的世界坐标设置写出，避免二次旋转。
已有 Z-up 数据若手动在 Studio 里转正，加载重新导出的 -Y-up 数据时需清除旧的额外 Transform。

| Dataset Format | 相机与位姿 | 点云 |
|---|---|---|
| COLMAP（默认） | `sparse/0/cameras.txt`、`images.txt`，OpenCV 世界到相机约定 | `points3D.txt` 与完整彩色 `initial_points.ply` |
| Photara Studio Native (.asfm) | `scene.asfm`，Photara 原生世界到相机旋转与相机中心 | `.asfm` 内的多视图稀疏轨迹；完整表面点保存在 PLY |
| Blender / Nerfstudio | `transforms.json`，OpenGL 相机到世界矩阵，逐帧内参 | 通过 `ply_file_path` 引用完整彩色 PLY |
| COLMAP + Nerfstudio | 同时写出两种通用格式，兼容旧版双选项 | 两者使用同一份表面点云 |

原生 ASFM 按 `photara/src/sfm/asfm.cpp` 的非紧凑 v4 二进制布局写出（长度前缀、FNV-1a 校验、
绝对图像路径（兼容 Studio 的图像目录基准与独立 ASFM 读取））。原生稀疏轨迹要求至少两个有效图像观测，插件会重新投影实际表面点并检查遮挡，
只写入通过验证的轨迹。仅单视图可见的几何点仍保留在完整 PLY 中；manifest 会报告两种点数，
不会伪造第二个观测。`.asfm` 可直接在 Studio 中打开或拖入视口。

当前 Studio 的 `project.set_dataset` 和 CLI `--splat-dataset` 读取器尚不支持 `.asfm`，因此选择原生
格式后插件只导出并提示在 Studio 打开，不启动自动训练。需要现有自动交付/训练流程时选择 COLMAP
或 Nerfstudio，插件会明确传递对应的模型目录或 JSON 文件，避免误读目录中残留的其他格式。

所有格式均保留 `photara_dataset.json` 中的完整采集记录，使“从已保存相机重建点云”不依赖是否导出了
`transforms.json`。切换格式不删除旧格式文件；本次 manifest 标记当前选择。旧 `.blend` 中保存的双
复选框设置会映射为对应选项；显式选择 Dataset Format 后以新选择为准。

360° 环绕使用所选相机作为镜头参数模板，并在渲染期间创建临时相机；
目标中心按当前场景帧求值。默认使用上半球、4 层，适合放置在水平承托面上的物体；
不会生成低于物体中心的机位。多层视点按仰角等距分层，各圈按周长分配张数，交替错开方位角，
顶部正俯视（完整球面的底部亦同）仅保留一个机位，避免极点重复采集，总张数保持不变。
单层模式保持单圈：上半球为 45°，其他模式为范围内最接近 0° 的仰角，不额外添加顶部机位。
完整球面需要至少 3 层才能在两个极点之外分配额外图像。现有 blend 的模式/层数保持已保存设置，
需手动切换 Upper Hemisphere。

Hemisphere Direction 控制整个环绕采集坐标系：Up (+Z)、Down (-Z)、Left (-X)、Right (+X)、
Front (-Y)、Back (+Y) 为世界轴方向，与当前视图朝向无关。半球机位位于目标中心朝选定方向的一侧。
选择 Custom Empty 并指定 Direction Empty 后，旋转 Empty 可任意调整方向：其世界旋转的局部 +Z
指向半球顶部，局部 X/Y 确定方位角；Empty 的位置和缩放不改变采集中心或半径，也不参与目标尺寸计算。
可使用带父级的 Empty，按当前场景帧求值。自定义仰角相对该采集坐标系的赤道面定义，Start Angle
绕其局部 Z 轴旋转；完整球面的分层方向也随之旋转。调整后重新点击 Preview Camera Positions，
确认机位再渲染。预览、实际渲染与仅导出位姿共用同一生成逻辑；导出元数据包含方向和旋转矩阵。
插件不自动识别承托面或避障。
渲染结束或取消后会恢复场景相机并移除临时相机。
环绕只支持透视相机，输出的每张图像仍对应标准针孔内参，可直接导出 COLMAP 和
`transforms.json`。

Indoor Box Grid 使用临时透视相机，所有图像固定在当前场景帧。Room Bounds 可选盒形 Mesh
或 Cube Empty；支持旋转与缩放。辅助边界盒应启用 Bounds Is Helper，采集期间会临时隐藏并恢复；
若选择实际房间壳体，关闭该选项。墙、家具和室内细节仍需保留渲染可见。

### 自由飞行录制相机点位

1. 在 3D 视图的 **Camera & Frames → Free Flight Recording** 设置 Point Distance（场景单位）、Point Angle 和 Sample Interval（秒）。移动距离或朝向变化达到任一阈值时新增点位。
2. 切换至透视视图，选择 **Camera Control**：默认 **First Person (Walk)** 保持高度进行水平移动，**Free Flight** 沿视线移动，**Manual View Navigation** 使用 Blender 自带导航。前两种开始后直接进入插件控制：鼠标转向、WASD 移动、Q/E 升降、Shift 加速；Enter 保存，Esc 取消并恢复视图。速度和鼠标灵敏度读取 Blender Preferences → Navigation → Walk 的设置。相机视图启动时，会用独立导航相机沿用当前构图和镜头参数，不修改原相机。
3. 普通透视视图下，可用 Enter 保存或 Esc 取消。Manual View Navigation 需先退出 Blender 自带导航，再点 **Stop & Generate**。起点和最终位置会保留；静止期间不重复采样。最多记录 10000 点，到达上限自动生成。**Cancel** 丢弃本次录制。
4. 自动创建独立的 **Photara Recorded Flight** 相机，每个点位对应一个整数帧，保存位置与四元数朝向，同时在 **Photara Camera Preview** 集合显示所有相机点位与朝向。预览不参与渲染。插件自动选中该相机、切换至 Camera Animation，并设置采集帧范围和步长；点击 Render & Export 即可逐点渲染。原相机动画不会被覆盖。
5. 已有录制或编辑动画后，可点击 **Preview Camera Positions** 按当前采集帧范围重新生成预览，旁边的垃圾桶按钮清除预览，保留录制相机和动画。

相机动画随 `.blend` 保存，可在 Camera 中重新选择以复用，也可在时间线继续编辑。录制为点位序列，不保留实际飞行速度或停留时间；渲染只采样整数帧。镜头参数在开始录制时固定：相机视图复制当前透视相机参数，普通透视视图使用视图焦距。正交视图不支持。录制期间保持当前场景和视图打开，切换场景或关闭视图会取消录制。

### 室内覆盖采集流程

1. 选择 Room Bounds、透视 Camera，并开启 Automatic Coverage。默认 3×3×3 层点阵、24mm 镜头，
   相邻方向角度重叠 35%。采集相机包括朝上、朝下方向；原始相机镜头不会改变。单层时高度从盒底计算。
2. 点击 Preview Camera Positions。规划以当前分辨率、像素比例和实际采集内参检查可见性：
   先布置基础全方向视点，再针对覆盖不足的表面添加相机位置或朝向。界面可处理事件并可取消规划。
3. 红色标记代表未观察到的表面样本，橙色表示独立观察位置不足。预览辅助对象不参与渲染。
   Solid 视图的 Color Type 需为 Material 或 Object 才能显示颜色。模型或参数改动后重新生成预览。
4. 每个样本默认要求 3 个独立相机位置，观察方向至少相差 5°，位置至少相距 Geometry Clearance。
   同一点原地转动相机不会增加此计数。采样按范围内三角形表面积加权，统计不是逐像素保证。
5. 调整 XYZ 点阵、Coverage Samples、Image Budget、范围和碰撞边距，直到覆盖达到要求。
   Require Coverage Target 会在存在未达标样本时阻止渲染；预算不足不会悄悄截断基础点阵。
6. Render & Export 会重新分析当前场景，渲染基础图像和补拍图像，导出内参、位姿和覆盖报告，
   完成后按 After Render 设置交付 Studio 或 CLI。停止按钮在当前帧完成后结束序列；Esc 可中止当前帧。
   中途结束保留已完成图像及其位姿，不启动训练。

`capture_coverage.json` 包含完整/未见/不足样本数、采集帧数、预算、补拍数量、相机位置和逐样本
观察数量，并区分规划覆盖和已完成图像覆盖。Coverage Samples 仅控制室内覆盖分析的采样密度。

### 统一表面初始点云

动画、环绕和室内点阵都使用同一套点云流程：从已保存图像采样像素中心，按该帧内参和位姿生成
射线，在对应场景帧的求值几何中保留首个可见表面交点，再读取该像素的渲染颜色。隐藏于渲染的物体
和辅助预览不作为遮挡；真正被前景挡住的背面不会被采样。点云位置来自实际几何，不沿相机轨迹随机生成。
同一体素内的样本合并并平均颜色，保存真实命中位置；超出预算时用确定性的蓄水池采样保留全序列中的点。
单视图的几何命中即可生成点，因为场景几何已知，无需依赖 SfM 三角化的双视图条件；不导出特征轨迹。

Output 面板中 Initial Points 控制点数上限（默认 20000），Point Rays per Image 控制每帧射线数量
（默认 1024），Point Merge Distance 控制世界空间合并距离（默认 0.01）。像素读取失败、背景或未命中
几何的像素不生成假点或灰色替代点。报告 initial_points_report.json 记录射线数、命中数、点数和读取失败。
COLMAP points3D.txt 和 initial_points.ply 使用同一份彩色点；transforms.json 包含 PLY 路径、场景帧和裁剪范围。
PNG/JPEG 等保留保存后的像素颜色；EXR 线性 HDR 转为 8 位 sRGB 并裁剪高光，不对应 AgX/Filmic 显示效果。

现有数据无需重渲染：打开采集使用的原场景、设置 Output Root，点击 Rebuild Point Cloud from Saved Cameras。
它读取数据集 transforms.json 的相机，不会用当前场景相机覆盖原位姿；成功生成非空点云后，先将旧点云与
元数据备份到 point_cloud_backups/，再写入点云并更新 PLY 引用。原图片、cameras.txt 和 images.txt 保持不变。
旧版数据的场景帧从 manifest 推导；必须保留采集时的场景几何和可见性。完成后在 Studio 重新加载数据集。
普通“仅导出位姿”仍会重新求值当前相机，因此修复已有数据应使用上述独立重建按钮。

表面射线把材质视为不透明，不模拟玻璃透射、镜面虚像、体积、景深或运动模糊；需使用一致的视口/渲染几何。
动画模式逐帧求值几何，环绕模式固定在源场景帧。静态 3DGS 无法表达运动物体，运动区域可能出现多时刻叠影。
最终外观仍由渲染图像训练。

覆盖分析把网格视为不透明几何，暂不模拟玻璃、镜面或体积材质；需使用一致的视口/渲染修改器级别。
渲染可见但视口隐藏/排除的网格会阻止分析，需先启用视口可见性；分析和采集期间不要编辑场景。
闭合房间壳体内部允许布点，独立封闭家具内部会排除；复杂自交/非流形模型可能需要修复或调整边界。
完全封闭且没有有效相机能看到的内表面会继续显示缺口。角度重叠不能保证不同空间位置之间的图像
重叠，小物体、狭窄遮挡区域需提高采样和点阵密度。覆盖达标表示采样几何满足设置，实际 3DGS
效果还取决于纹理、光照、分辨率、训练参数和需要重建的可见空间。

## 输出目录结构

```text
photara_dataset/
├── images/
│   ├── frame_00000000.png
│   └── ...
├── sparse/0/                  # COLMAP 文本模型（3DGS 训练主格式）
│   ├── cameras.txt            # CAMERA_ID PINHOLE W H fx fy cx cy
│   ├── images.txt             # IMAGE_ID QW QX QY QZ TX TY TZ CAMERA_ID NAME
│   └── points3D.txt           # 所有采集模式的可见表面初始点
├── initial_points.ply         # 室内几何初始化（存在有效多视图样本时）
├── capture_coverage.json      # 室内采样覆盖报告与未完成状态
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
python -m unittest discover -s tests -v
blender -b --factory-startup --python-exit-code 1 --python tests/blender_indoor_capture.py
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

## 许可证

本项目采用 [Apache License 2.0](LICENSE) 授权。
