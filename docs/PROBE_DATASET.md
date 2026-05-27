# Probe 数据集设计

这个数据集用于把 150 张已知输入图交给菲林/光栅制作厂，再用厂商返回的合成 TIFF 反推他们的交织、几何预处理、色彩处理和挂网/RIP 行为。

默认规格：

- 图片数量：150 张
- 单张尺寸：2160 x 3651
- 格式：RGB 8-bit PNG
- 命名：`probe_000.png` 到 `probe_149.png`
- 生成脚本：`utils/generate_probe_dataset.py`
- 输出目录默认：`probe_dataset_v001/`
- 连续调视角码：从 16-236 安全范围内的 6 档 RGB 立方体中贪心选出的高间距调色板，150 个视角的最小 RGB 欧氏距离为 44。

## 核心思路

这 150 张图不是 150 个独立测试批次，而是一个 150 视角 probe 作业。每张图内部包含相同的区域布局，但部分区域会按视角编号写入不同的 RGB、灰度、二进制码和脉冲线。

厂商返回一个合成 TIFF 后，可以在同一张结果图里同时读到：

- 是否发生了缩放、裁切、旋转、通道交换、gamma 或自动调色。
- 连续调 TIFF 中每个像素来自哪个输入视角。
- 交织方向、pitch、phase/offset、视角排序、是否反序。
- 1-bit TIFF 中的挂网类型、网角、线数/频率、tone curve、AM/FM 倾向。
- 交织和挂网是否在同一阶段处理，或者是否有中间连续调文件。

## 区域布局

| Y 范围 | 区域 | 用途 |
|---:|---|---|
| 0-360 | orientation_and_view_id_header | 角点定位、方向识别、视角编号、人眼可查的 RGB/灰度码 |
| 360-920 | binary_gray_code_view_id | 重复的局部 Gray-code barcode tile；用于在 1-bit TIFF 中做交织模型拟合，不依赖跨大 Y 区域拼 bit |
| 920-1340 | continuous_view_color_decode | 每个视角一整块唯一 RGB 颜色；连续调 TIFF 下最适合精确解出 view map |
| 1340-1940 | shared_coordinate_ramps | 所有视角相同的 RGB 坐标坡度；用来测缩放、裁切、旋转、通道、gamma |
| 1940-2300 | shared_resolution_frequency_chart | 所有视角相同的线对/棋盘图；用来测重采样、模糊和输出分辨能力 |
| 2300-3050 | shared_halftone_tone_scale | 所有视角相同的灰阶块和渐变；用来测挂网密度曲线、阈值和 AM/FM 特征 |
| 3050-3440 | shared_screen_angle_frequency_chart | 所有视角相同的 0/15/45/75 度频率图；用 FFT 估计网角、LPI 和龟纹风险 |
| 3440-3651 | view_dependent_impulse_footer | 每个视角不同的稀疏脉冲线；辅助识别视角排序、局部 phase 和边界行为 |

## 生成方式

快速小样验证：

```powershell
python utils\generate_probe_dataset.py --out probe_dataset_smoke --limit 2 --force
```

完整数据集：

```powershell
python utils\generate_probe_dataset.py --out probe_dataset_v001 --force
```

脚本会生成：

```text
probe_dataset_v001/
  README_FOR_FACTORY.md
  probe_manifest.json
  images/
    probe_000.png
    probe_001.png
    ...
    probe_149.png
```

`probe_dataset*/` 已加入 `.gitignore`，这些大文件不应提交到 Git。

## 给厂商的建议

把 `README_FOR_FACTORY.md` 一起发给厂商。核心要求是：

- 按 `probe_000.png ... probe_149.png` 顺序作为 150 个视角输入。
- 使用厂商真实生产默认流程。
- 不要人工裁切、缩放、旋转、锐化、降噪、调色或重排文件。
- 如果能导出挂网前的连续调 TIFF，请同时返回。
- 如果只能返回一种文件，请返回最终用于菲林生产的 TIFF。

## 返回 TIFF 后的反推顺序

1. 先检查像素尺寸和四角定位图，确定是否有缩放、裁切、旋转或边界填充。
2. 如果有连续调 TIFF，优先使用 `continuous_view_color_decode` 区域按 RGB 最近邻解出每个像素的视角编号。这个区域没有内部定位线，整块都是视角码，避免共享图案污染 view map。
3. 对解出的 view map 拟合交织模型，重点估计 pitch、angle、offset、channel term 和视角顺序。
4. 用 `shared_coordinate_ramps` 校准厂商是否改变坐标、通道顺序、gamma 或色彩空间。
5. 如果只有 1-bit TIFF，先用 `shared_halftone_tone_scale` 和 `shared_screen_angle_frequency_chart` 估计挂网，再用 `binary_gray_code_view_id` 的局部 barcode tile 和 `view_dependent_impulse_footer` 做交织模型拟合。
6. 最后把估计出的交织参数代入本仓库的合成逻辑，重新生成预测图，与厂商 TIFF 做差分验证。

## 局限

如果厂商只返回 1-bit TIFF，而且没有任何连续调中间文件，交织和挂网会被耦合在一起。这个 probe 仍然可以估计 pitch、网角、tone curve 和粗略视角相位，但逐像素精确恢复会明显更难。最理想的返回物是连续调合成 TIFF 加最终 1-bit TIFF 两个文件。
