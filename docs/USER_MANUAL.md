# Light Field Render User Manual

This manual describes how to install and use the `Light Field Render` Blender add-on.

## 1. Installation

Download the add-on ZIP from the GitHub Release page. The file should be named like:

```text
light_field_render-v0.1.8.zip
```

Install it in Blender:

1. Open Blender 4.2 LTS or newer.
2. Open `Edit > Preferences > Add-ons`.
3. Click `Install...`.
4. Select the ZIP file.
5. Enable `Light Field Render`.
6. Open the 3D Viewport sidebar with `N`.
7. Use the `光场` tab.

## 2. Main Concepts

The add-on creates a row of cameras for light-field rendering. All cameras are arranged linearly and use off-axis projection so that their frustums converge on a shared focal plane.

Important terms:

- `相机数量`: total number of views in the light-field camera array.
- `焦平面距离`: distance from the camera array to the focal plane.
- `阵列张角`: angular coverage of the camera array.
- `景深范围`: visual depth volume around the focal plane.
- `焦距`: Blender camera lens focal length in millimeters.
- `传感器宽度`: Blender camera sensor width in millimeters.
- `输出分辨率`: output resolution for each rendered view.
- `输出格式`: `PNG`, continuous `TIFF`, or halftoned `1-bit 菲林 TIFF`.
- `最终交付输出`: current-frame delivery workflow that uses physical size in mm plus PPI to generate final interlaced files.
- `交付宽度` / `交付高度` / `PPI`: customer delivery size. Final pixels are calculated as `round(mm / 25.4 * PPI)`.

## 3. Creating A Camera Array

1. Open the `光场` tab in the 3D Viewport sidebar.
2. Set the physical geometry parameters.
3. Set camera intrinsics.
4. Click `创建光场相机`.

After the camera system exists, slider edits are intentionally lightweight. They mark the camera array as changed, but they do not rebuild or update every camera on every mouse movement. Click `应用相机参数` to apply the pending camera geometry and intrinsic changes. This avoids UI stalls while dragging sliders such as focal length, focal distance, opening angle, or camera count.

The add-on creates:

- A `LightField_Control` empty object.
- A focal-plane helper object.
- A display-depth helper object.
- A camera array named with the `LF_Camera_` prefix.

The helper objects are visualization aids and are not intended to be rendered.

## 4. Previewing Views

After the camera system is created, use the preview controls in the add-on panel:

- Select an active camera index.
- Jump to first or last camera.
- Step to previous or next camera.

The active camera becomes the scene camera.

## 5. Rendering A Single Frame

1. Set the scene to the target frame.
2. Set the output directory.
3. Set output resolution.
4. Select output format.
5. Click the single-frame render button.

The add-on renders all cameras for the current frame. Output is grouped by frame:

```text
light_field_output/
  frame_0001/
    camera_000.png
    camera_001.png
    ...
```

If rendering is interrupted, the add-on checks existing output files and resumes from the first missing camera image.

The resume check is format-aware. For `1-bit Film TIFF`, an existing `.tif` is only treated as complete if it is actually a 1-bit TIFF, so a previous continuous TIFF render will not be mistaken for a finished film output.

## 6. Rendering Animation

1. Set `开始帧` and `结束帧` in the add-on panel.
2. Set the output directory.
3. Click the animation render button.

The add-on renders the selected frame range for each camera. Output is grouped by camera:

```text
light_field_output/
  camera_000/
    frame_0001.png
    frame_0002.png
    ...
  camera_001/
    frame_0001.png
    frame_0002.png
    ...
```

## 7. Output Formats

The add-on supports three output modes:

- `PNG`: standard continuous-tone PNG image output.
- `TIFF`: standard continuous-tone TIFF output written by Blender.
- `1-bit 菲林 TIFF`: renders a temporary continuous PNG source, converts it to a black/white halftoned 1-bit TIFF, and deletes the temporary source unless `保留连续调源图` is enabled.

For `1-bit 菲林 TIFF`, the output is a single-channel 1-bit TIFF intended for film/RIP workflows that need pure black/white dots instead of continuous tone. The TIFF writer stores `BitsPerSample=1`, `SamplesPerPixel=1`, `Compression=none`, and inch-based DPI metadata.

Halftone controls:

- `FM / 误差扩散`: dispersed fixed-size dots. This is the default because it is generally less prone to visible moire in lenticular/light-field workflows.
- `AM / 聚集网点`: traditional clustered dots controlled by `DPI`, `LPI`, screen angle, and dot shape.
- `DPI`: output resolution metadata and AM cell-size basis.
- `LPI`: AM screen ruling, used only in AM mode.
- `网角`: AM screen angle in degrees.
- `网点形状`: round, diamond, or ellipse in AM mode.
- `Gamma`: luminance correction before halftoning.

## 8. Final Delivery Output

The `最终交付输出` panel is for the print/film deliverable. It is intentionally separate from Blender's source-view render resolution:

- `输出分辨率 W/H` controls each `camera_###.png` source view rendered by Blender.
- `交付宽度 mm`, `交付高度 mm`, and `PPI` control the final interlaced delivery pixel size.

For example, `210 mm x 297 mm @ 300 PPI` produces approximately `2480 x 3508` final pixels. Blender does not need to render every camera at that final size; the add-on resamples the source views during interlacing.

Workflow:

1. Set the normal camera-array and source-view render settings.
2. In `最终交付输出`, enter `交付宽度 mm`, `交付高度 mm`, and `PPI`.
3. Set interlace parameters:
   - `PE`: original PE formula parameter from the existing interlace workflow.
   - `Angle (°)`: interlace angle in degrees; the add-on converts it to radians internally.
   - `Offset`: original formula offset.
   - `反转视角顺序`: maps view 0 to `camera_N-1` when enabled.
4. Configure the existing `1-bit 菲林 TIFF` halftone settings if film output is needed.
5. Click `生成当前帧交付文件`.

The output folder is:

```text
light_field_output/
  frame_0001/
    camera_000.png
    camera_001.png
    ...
  delivery/
    frame_0001/
      interlaced.tif              # optional
      interlaced_preview.png
      film_1bit.tif
      delivery_manifest.json
```

Files:

- `interlaced.tif`: optional full-size continuous-tone 8-bit RGB TIFF, uncompressed, with PPI written as TIFF DPI metadata. If the RGB image exceeds classic TIFF limits, this file is written as BigTIFF automatically. Enable `输出连续调 interlaced.tif` only when the factory or debugging workflow needs the continuous-tone interlaced image.
- `interlaced_preview.png`: quick preview PNG with max edge 2048px.
- `film_1bit.tif`: full-size single-channel 1-bit black/white TIFF using the selected FM/AM halftone settings.
- `delivery_manifest.json`: records plugin version, frame, mm/PPI/pixel size, source resolution, camera count, interlace parameters, halftone parameters, warnings, file names, and elapsed time.

Safety behavior:

- If source view PNGs already exist and match the current camera count and source resolution, they are reused.
- If source view PNGs are missing, they are rendered before interlacing.
- If camera or output settings are dirty, the add-on applies them and rerenders source views.
- If the final output exceeds 100 megapixels, `确认生成大图` must be checked.
- Very large deliverables can still take a long time if `输出连续调 interlaced.tif` is enabled. `194 x 345 mm @ 4000 PPI` is about `30551 x 54331` pixels, roughly 5 GB for the continuous RGB TIFF alone, so the add-on uses BigTIFF and reports row progress while writing.
- Release ZIPs bundle Blender-compatible NumPy and the Windows native accelerator by default. If both are available, AM delivery with zero-degree interlace uses the native path for high-resolution film output.
- If the final output is more than 2x larger than the source-view resolution on either axis, the panel warns that clarity may be insufficient.
- Failed or stopped generation removes temporary `.tmp` files and writes `delivery_error.log`.

## 9. Recommended Workflow

1. Test with a small camera count such as `5` or `9`.
2. Verify that the focal plane and depth helper volume match the target scene.
3. Render a low-resolution single frame.
4. Check left, center, and right views.
5. If final delivery output is required, test `最终交付输出` with a small physical size and low PPI first.
6. Increase camera count and resolution.
7. Generate the final current-frame delivery files.

## 10. Troubleshooting

If the add-on panel is not visible:

- Confirm the add-on is enabled in Blender preferences.
- Open the 3D Viewport sidebar with `N`.
- Look for the `光场` tab.

If rendering does not start:

- Create the light-field camera system first.
- Check that the output path is valid.
- Confirm that another render task is not already running.

If output files are incomplete:

- Re-run the same render command.
- The add-on will resume from the first missing camera output.

If the active camera does not change:

- Confirm the camera array exists.
- Recreate the light-field camera system if Blender undo/redo removed helper objects.

If changing width or height appears not to affect Blender scene settings:

- Click `应用输出设置`, or start a render. The render operators always apply `宽度` and `高度` before rendering.
- Width and height are stored independently and are no longer coupled through a live update callback.

If slider dragging feels delayed:

- This is intentional. Heavy camera-array updates are deferred. Click `应用相机参数`, or enable `拖动结束后自动应用` if you want delayed automatic application.

If final delivery generation is refused:

- Check that `交付宽度 mm`, `交付高度 mm`, and `PPI` are all greater than zero.
- If the final output exceeds 100 megapixels, enable `确认生成大图`.
- Check `delivery_error.log` inside the delivery frame folder for the detailed failure.

If final delivery looks soft:

- Increase the normal `输出分辨率 W/H` for source views.
- The final delivery stage can resample source views to a larger size, but it cannot create detail that was not rendered by Blender.

## 11. Notes For Release Builds

The Blender add-on ZIP must contain the add-on package folder at the ZIP root:

```text
light_field_plugin/
  __init__.py
  core/
  operators/
  panels/
  properties/
```

Use `scripts/build_release.ps1` to create this layout automatically.
