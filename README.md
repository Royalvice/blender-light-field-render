# Light Field Render for Blender

`Light Field Render` 是一个 Blender 4.2 LTS 插件，用于创建线性离轴光场相机阵列、批量渲染多视角序列图，并生成用于光栅/菲林交付的交织图和 1-bit TIFF。

仓库现在以 Blender 插件为主。旧 Three.js 可视化工具已归档在 `archive/web_viz/`，仅作为参考资料保留。

## 功能

- 创建可配置的线性光场相机阵列。
- 使用 Blender 相机 `shift_x` 实现离轴投影。
- 可视化焦平面和显示深度边界框。
- 渲染当前帧或动画帧范围内的所有视角。
- 源视角图支持 `JPG`、`PNG`、连续调 `TIFF`，默认 `JPG quality=95` 用于厂商交付。
- 支持整像素 PE 交织，不再使用 RGB 子像素交织。
- 支持只输出连续调交织图，也支持从已有 `interlaced.tif` 单独生成菲林 TIFF。
- 最终 `film_1bit.tif` 为未压缩 1-bit TIFF，`PhotometricInterpretation=1`，解码语义为 `0=black`、`1=white`。
- 发布 ZIP 内置 Blender 兼容 NumPy 和 Windows native 加速 DLL，用户不需要手动安装 Python 包。
- 大图生成在后台线程执行，带进度显示、停止按钮、临时文件清理和错误日志。

## 安装

从 GitHub Release 下载：

```text
light_field_render-v0.1.20.zip
```

在 Blender 中安装：

1. 打开 `Edit > Preferences > Add-ons`。
2. 点击 `Install...`。
3. 选择 release ZIP。
4. 启用 `Light Field Render`。
5. 在 3D Viewport 右侧栏 `光场` 面板中使用插件。

开发模式下也可以直接安装仓库中的 `light_field_plugin/` 包。

## 仓库结构

```text
light_field_plugin/      Blender 插件包
docs/                    用户手册、发布说明、技术文档
scripts/                 打包、集成测试、校准脚本
tests/                   Python 单元测试
archive/web_viz/         已归档的 Three.js 可视化工具
utils/                   辅助工具和样例数据
```

## 快速开始

1. 启用插件后打开 3D Viewport 右侧栏 `光场`。
2. 设置相机数量、焦平面距离、开角、焦距、传感器宽度。
3. 点击 `创建光场相机`。
4. 修改参数后点击 `应用相机参数`，避免拖动滑条时实时重建导致卡顿。
5. 设置输出路径、输出分辨率和源视角格式，默认建议 `JPG`。
6. 点击 `渲染当前帧` 或 `渲染动画` 输出 `camera_###.jpg` 序列。
7. 在 `最终交付输出` 面板设置交付物理尺寸、PPI、PE、角度、偏移和视角顺序。
8. 根据需要点击 `生成当前帧交付文件`、`只生成连续调交织图` 或 `从交织图生成菲林 TIFF`。

完整说明见 [docs/USER_MANUAL.md](docs/USER_MANUAL.md)。

## 最终交付输出

源视角渲染分辨率和最终交付尺寸是分离的：

- `输出分辨率 W/H` 控制每张 `camera_###.jpg` 的尺寸。
- `交付宽度 mm`、`交付高度 mm`、`PPI` 控制最终交织/菲林文件尺寸。
- 最终像素尺寸为 `round(mm / 25.4 * PPI)`。

典型输出目录：

```text
output_path/
  frame_0001/
    camera_000.jpg
    camera_001.jpg
    ...
  delivery/
    frame_0001/
      interlaced.tif
      interlaced_preview.png
      film_1bit.tif
      delivery_manifest.json
      halftone_calibration_report.json
```

按钮语义：

- `生成当前帧交付文件`：渲染或复用当前帧源视角图，执行交织和挂网，输出 `film_1bit.tif`、预览图和 manifest。是否写 `interlaced.tif` 由 `输出连续调 interlaced.tif` 控制。
- `只生成连续调交织图`：只输出 `interlaced.tif`、`interlaced_preview.png`、`delivery_manifest.json`，并删除该帧目录下过期的 `film_1bit.tif`。
- `从交织图生成菲林 TIFF`：读取已有未压缩 RGB `interlaced.tif`，使用固定 `LBY_row_threshold_v1` profile 生成 `film_1bit.tif` 和 `halftone_calibration_report.json`，不重新渲染、不重新交织。
- `停止交付生成`：请求后台任务停止，清理 `.tmp` 文件并恢复 UI 状态。

大图连续调 `interlaced.tif` 会自动使用 BigTIFF。例如 `194 x 345 mm @ 4000 PPI` 约为 `30551 x 54331` 像素，RGB 连续调 TIFF 约 5 GB，经典 TIFF 无法容纳。

## v0.1.20 交付语义

- 源视角默认输出 `JPG`，文件名为 `camera_000.jpg`、`camera_001.jpg` 等。
- JPG 渲染会临时使用 Blender `Standard` color management，完成后恢复原场景设置。
- Native 快速路径直接读取磁盘 JPG，不在 UI 主线程做 JPG 到 PNG 的临时转换。
- 交织为整像素交织：每个最终 RGB 像素选择一个源视角，并复制完整 RGB 值。
- PE 按物理线数解释，输出周期为 `PPI / PE`。
- 最终打印算法只暴露 `LBY 行阈值屏`。
- `LBY_row_threshold_v1` 是固定、全局、可解释的 18 px 水平行阈值屏，不包含针对单个输入文件的像素拷贝或特殊分支。
- 拟合参数：period `18 px`，Y phase `0`，gamma `0.25`，density `0.25`，bias `-0.05`，固定 18 项 row threshold table。
- 对 `618空间_dats_dats.tif` 的全尺寸验证：目标尺寸 `30551 x 54342`，global mismatch `1.6745%`，target-active mismatch `3.7562%`，target-active mask 为 LBY 黑像素膨胀 32 px。
- 150 张 `2160 x 3651` JPG、`4000 PPI`、`PE=52.64`、反转视角顺序的 native 全流程本次实测生成时间约 `18.55s`。

## 构建 Release ZIP

从仓库根目录执行：

```powershell
.\scripts\build_release.ps1
```

输出文件位于 `dist/`，用于 GitHub Release asset 和 Blender 插件安装。默认会把 Blender 兼容 NumPy 和 Windows native DLL 打包进 ZIP；只有开发调试时才建议使用 `-NoBundleNumpy`。

## 归档 Web 可视化工具

旧浏览器版 Three.js 可视化工具位于 `archive/web_viz/`：

```powershell
cd archive\web_viz
python -m http.server 8000
```

然后打开 `http://localhost:8000`。

## License

当前仓库没有独立 `LICENSE` 文件。公开分发前如需明确开源协议，应补充 license。
