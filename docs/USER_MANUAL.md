# Light Field Render 用户手册

本文档对应 `Light Field Render v0.1.24`，目标 Blender 版本为 4.2 LTS。

## 1. 安装

下载 GitHub Release 中的插件 ZIP：

```text
light_field_render-v0.1.24.zip
```

安装步骤：

1. 打开 Blender。
2. 进入 `Edit > Preferences > Add-ons`。
3. 点击 `Install...`。
4. 选择 `light_field_render-v0.1.24.zip`。
5. 勾选启用 `Light Field Render`。
6. 在 3D Viewport 右侧栏打开 `光场` 面板。

插件 ZIP 已包含 Windows native 加速 DLL 和 Blender 兼容 NumPy，普通用户不需要安装 Python 包。

## 2. 面板概览

主要面板：

- `光场相机`：创建和管理线性光场相机阵列。
- `输出设置`：设置源视角图的输出路径、输出格式、渲染分辨率和 JPG 质量。
- `渲染控制`：渲染当前帧或动画。
- `1-bit 菲林 TIFF`：设置最终 print TIFF 的挂网参数。
- `最终交付输出`：按物理尺寸和 PPI 生成最终交织/菲林交付文件。

## 3. 创建光场相机

基础流程：

1. 设置 `相机数量`。
2. 设置 `焦平面距离`、`开角`、`焦距`、`传感器宽度`。
3. 点击 `创建光场相机`。
4. 如后续拖动参数，拖动结束后点击 `应用相机参数`。

插件默认不会在滑条拖动中实时重建相机阵列，以避免 Blender UI 卡死。需要实时应用时可启用 `拖动结束后自动应用`，但大相机数量场景不建议开启。

## 4. 源视角渲染

源视角是厂商或交付流程中使用的 `camera_###` 序列图。

推荐设置：

- `输出格式`: `JPG`
- `JPG 质量`: `95`
- `输出分辨率 W/H`: 每张源视角图的像素尺寸，例如 `2160 x 3651`

输出结构：

```text
output_path/
  frame_0001/
    camera_000.jpg
    camera_001.jpg
    ...
```

`JPG` 渲染会临时把 Blender color management 切到 `Standard`，完成后恢复原场景设置，减少额外 tone mapping 对厂商交付的影响。

## 5. 最终交付尺寸

最终交付尺寸独立于源视角渲染分辨率：

- `输出分辨率 W/H` 只控制每张源视角图。
- `交付宽度 mm`、`交付高度 mm`、`PPI` 控制最终交织图和菲林图尺寸。

最终像素尺寸计算公式：

```text
width_px  = round(width_mm  / 25.4 * ppi)
height_px = round(height_mm / 25.4 * ppi)
```

例如 `194 x 345 mm @ 4000 PPI` 会生成约 `30551 x 54331` 像素的交付图。若需要严格匹配已有厂商样张高度 `54342`，应使用对应的物理高度 `345.0717 mm`。

## 6. 交织参数

最终交织使用整像素交织：

- 每个输出 RGB 像素只来自一个源视角。
- 不再把 R/G/B 子像素分别分配给不同视角。
- PE 按物理线数处理，输出周期为 `PPI / PE`。

参数：

- `PE`: 光栅/交织线数参数。
- `Angle`: 交织角度，单位度。
- `Offset`: 相位偏移。
- `反转视角顺序`: 将 view 0 映射到最后一张源视角，用于匹配当前厂商样张。

## 7. 1-bit 菲林 TIFF

最终 `film_1bit.tif` 是单通道 1-bit 黑白 TIFF：

- `BitsPerSample=1`
- `SamplesPerPixel=1`
- `Compression=none`
- `PhotometricInterpretation=1`
- 解码语义为 `0=black`、`1=white`

默认打印算法是 `标准AM菲林`，用于按传统聚集网点挂网生成自然灰阶：

- `PPI`: 最终交付文件像素密度，默认 `4000`，也会写入 TIFF DPI 元数据。
- `LPI`: AM 挂网线数，默认 `200`；网点单元尺寸约为 `PPI / LPI`。
- `网角`: 默认 `45°`。
- `网点形状`: 默认圆形。
- `Gamma`: 标准 AM 默认 `1.0`，即线性亮度响应。

`标准AM菲林` 会先按 Rec.709 亮度公式把 RGB 转成单黑版灰度，再用黑白网点的大小和疏密模拟连续灰度。它不追求和厂商 LBY 返回件逐 bit 一致，目标是灰阶渐变自然、没有大块色带。

`LBY v1 行阈值屏` 和 `LBY v2 探针反推` 仍保留为旧结果对照和反推实验入口。选择 LBY 时才显示 `线周期 px`、`Y 相位 px` 和 `密度` 参数。

可以用标准 AM 样张脚本生成快速验收图：

```powershell
python scripts\generate_standard_am_sample.py --output-dir standard_am_sample
```

脚本会输出连续调 PNG、真实 1-bit TIFF 和 manifest，用于检查灰阶块、渐变和细线区域。

## 8. 最终交付按钮

输出目录：

```text
output_path/
  delivery/
    frame_0001/
      interlaced.tif
      interlaced_preview.png
      film_1bit.tif
      delivery_manifest.json
      halftone_calibration_report.json
```

按钮说明：

- `生成当前帧交付文件`：渲染或复用当前帧源视角图，执行交织和挂网，输出最终交付文件。
- `只生成连续调交织图`：只输出 `interlaced.tif`、`interlaced_preview.png` 和 `delivery_manifest.json`，不输出 `film_1bit.tif`。
- `从交织图生成菲林 TIFF`：读取已有 `interlaced.tif`，只执行挂网，输出 `film_1bit.tif` 和 `halftone_calibration_report.json`。
- `停止交付生成`：请求后台生成任务停止，清理临时文件，并让 UI 回到可操作状态。

建议工作流：

1. 先生成源视角 JPG 并检查左、中、右视角。
2. 点击 `只生成连续调交织图`，检查 `interlaced_preview.png`。
3. 点击 `从交织图生成菲林 TIFF`，单独检查挂网结果。
4. 流程稳定后再使用 `生成当前帧交付文件` 一步完成。

## 9. 大图和性能

大图注意事项：

- 超过 100MP 的最终输出需要勾选 `确认生成大图`。
- 连续调 `interlaced.tif` 可能非常大，超过 classic TIFF 限制时会自动写 BigTIFF。
- 如果厂商只需要最终 `film_1bit.tif`，建议关闭 `输出连续调 interlaced.tif`，节省磁盘和时间。
- native 快速路径适用于 zero-degree LBY 行阈值屏交付，使用 Windows 系统线程，不要求用户安装 Visual Studio、OpenMP 或 `VCOMP140.DLL`。

已验证大图参数：

```text
source views: 150 JPG, 2160 x 3651
final size:   30551 x 54342
ppi:          4000
PE:           52.64
view order:   reversed
generation:   18.55s native direct generation on validation workstation
```

## 10. v0.1.19 LBY 验证结果

对厂商目标 `618空间_dats_dats.tif` 的全尺寸验证：

```text
target size:            30551 x 54342
global mismatch:        1.6745%
target-active mismatch: 3.7562%
active mask:            target black pixels dilated by 32 px
generated black ratio:  15.55%
target black ratio:     15.31%
```

这不是 bitwise clone，也不是按输入图片作弊的逐像素复制。它是稳定的行阈值屏近似算法，目标是给菲林打印提供可解释、可复现、误差小于 5% active mismatch 的挂网流程。

## 11. 错误处理

插件会显式失败而不是静默兜底：

- 不支持的 TIFF 压缩或 tiled TIFF 会报错。
- 缺少源视角图时会按当前设置重新渲染。
- 大图未确认时会拒绝生成。
- 停止或失败时会删除 `.tmp` 临时文件。
- 失败详情写入 `delivery_error.log`。

## 12. 发布和开发

构建 release ZIP：

```powershell
.\scripts\build_release.ps1
```

基础测试：

```powershell
python -m compileall -q light_field_plugin scripts\calibrate_lby_linescreen.py scripts\blender_integration_test.py
python -m unittest discover -s tests -v
```

Blender 集成测试：

```powershell
blender --background --factory-startup --python scripts\blender_integration_test.py
```
