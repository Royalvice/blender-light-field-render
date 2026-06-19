# Light Field Render for Blender

Light Field Render is a Blender add-on for creating and rendering a linear off-axis light-field camera array. It is intended for light-field display content generation, multi-view rendering, and camera-array visualization inside Blender.

The repository is now organized around the Blender add-on. The older Three.js visualizer is preserved under `archive/web_viz/` for reference, but it is no longer the primary project entry point.

## Features

- Creates a configurable linear light-field camera array.
- Uses Blender camera `shift_x` to implement off-axis projection.
- Visualizes the focal plane and display depth volume with non-rendered helper objects.
- Supports single-frame rendering across all cameras.
- Supports animation rendering across all cameras and a selected frame range.
- Supports JPG, PNG, and continuous TIFF source-view rendering. JPG is the default vendor handoff format.
- Supports whole-pixel PE interlacing and LBY-like 1-bit print TIFF output.
- Generates final delivery interlace files from customer physical size in mm plus PPI.
- Outputs 2048px preview PNG, 1-bit film TIFF, and a JSON manifest by default, with optional full-size continuous interlaced TIFF.
- Automatically writes BigTIFF for continuous interlaced output when the file exceeds classic TIFF limits.
- Uses bundled NumPy and a Windows native accelerator where applicable; no user Python package install is required.
- Avoids UI stalls by deferring heavy camera-array updates while sliders are dragged.
- Tracks render progress and can resume from existing output files.

## Requirements

- Blender 4.2 LTS or newer.
- No third-party Python packages are required for the Blender add-on.

## Install

Use the release ZIP asset named like:

```text
light_field_render-v0.1.17.zip
```

Then install it in Blender:

1. Open Blender.
2. Go to `Edit > Preferences > Add-ons`.
3. Click `Install...`.
4. Select the release ZIP file.
5. Enable `Light Field Render`.
6. Open the 3D Viewport sidebar and use the `光场` tab.

For development, you can also install the add-on by pointing Blender at the `light_field_plugin/` package in this repository.

## Repository Layout

```text
light_field_plugin/      Blender add-on package
docs/                    User manual and technical notes
scripts/                 Release packaging scripts
archive/web_viz/         Archived Three.js visualizer
utils/                   Auxiliary light-field image utilities and sample data
```

## Quick Start

1. Enable the add-on in Blender.
2. Open the 3D Viewport sidebar with `N`.
3. Select the `光场` tab.
4. Set camera count, focal plane distance, opening angle, focal length, and sensor width.
5. Click `创建光场相机`.
6. If you change camera parameters after creation, click `应用相机参数`.
7. Preview cameras with the active camera controls.
8. Set an output directory and output format. The output format selector is labeled `输出格式`.
9. Run single-frame or animation rendering with `渲染当前帧` or `渲染动画`.

See [docs/USER_MANUAL.md](docs/USER_MANUAL.md) for the full workflow.

## Final Delivery Output

The `最终交付输出` panel separates Blender source-view rendering from the final print/film size. Use the existing `输出分辨率 W/H` for each rendered camera view, then set the customer delivery size with:

- `交付宽度 mm`
- `交付高度 mm`
- `PPI`

The add-on calculates the final pixel size as `round(mm / 25.4 * PPI)`. `生成当前帧交付文件` renders or reuses the current frame source views, interlaces them with the PE/Angle/Offset parameters, and writes the final film-delivery files:

```text
output_path/
  delivery/
    frame_0001/
      interlaced.tif
      interlaced_preview.png
      film_1bit.tif
      delivery_manifest.json
```

`interlaced.tif` is optional and disabled by default for large-delivery speed. Leave `输出连续调 interlaced.tif` off when the factory only needs the final 1-bit film TIFF plus preview; enable it when a continuous-tone interlaced BigTIFF is needed for debugging or vendor handoff.

Use `只生成连续调交织图` when you only need the continuous-tone interlaced image. That button writes `interlaced.tif`, `interlaced_preview.png`, and `delivery_manifest.json`, and it intentionally skips halftoning and `film_1bit.tif`.

This avoids forcing Blender to render every camera at the final print resolution.

For very large delivery sizes, the add-on switches `interlaced.tif` to BigTIFF automatically. For example, `194 x 345 mm @ 4000 PPI` is about `30551 x 54331` pixels, so the RGB continuous TIFF is roughly 5 GB and cannot be represented by classic TIFF.

The large-delivery path has been stress-tested with 150 JPG source views at `2160 x 3651`, `30551 x 54342` final pixels, `4000 PPI`, `PE=52.64`, reversed view order, and the native `LBY-like近似` path. The current Windows build decodes JPG directly with WIC in the background worker, then generates `film_1bit.tif`, preview, and manifest in about `12.5s` total on the test workstation.

### v0.1.17 Delivery Semantics

- Source-view output defaults to `JPG` at quality `95`; file names are `camera_000.jpg`, `camera_001.jpg`, and so on.
- JPG source rendering temporarily forces Blender color management to `Standard` and restores the original scene settings after rendering.
- Final delivery reads those disk JPG files directly through the native Windows decoder before interlacing, so the UI no longer needs to convert JPGs to temporary PNGs on the main thread.
- Interlacing is whole-pixel: one final RGB pixel is selected from one source view, then copied to all RGB channels. The PE period is calculated as `PPI / PE`.
- `film_1bit.tif` uses uncompressed 1-bit TIFF with `PhotometricInterpretation=1`, so decoded pixels follow `0=black` and `1=white`.
- The exposed print algorithm is `LBY-like近似`. It is a deterministic whole-pixel interlace plus AM diamond clustered-dot screen. It is intended to produce real 1-bit dots instead of the previous single-threshold line-art output. It remains an approximation, not a bitwise clone of the factory RIP.

## Build Release ZIP

From the repository root:

```powershell
.\scripts\build_release.ps1
```

By default the release script attempts to bundle Blender's compatible NumPy under `light_field_plugin/_vendor/` inside the ZIP so users do not need to install Python packages manually. Use `-NoBundleNumpy` only for a slim development ZIP.

The output will be written to `dist/` and is suitable for GitHub Releases and Blender add-on installation.

## Archived Web Visualizer

The old browser-based Three.js visualizer is retained at `archive/web_viz/`. To run it:

```powershell
cd archive\web_viz
python -m http.server 8000
```

Then open `http://localhost:8000`.

## License

No license file is currently included. Add a `LICENSE` file before public distribution if this project should have explicit open-source licensing terms.
