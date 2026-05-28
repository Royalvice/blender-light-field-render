# Release Checklist

Use this checklist when publishing a GitHub Release.

## Preflight

1. Confirm `light_field_plugin/__init__.py` has the intended `bl_info["version"]`.
2. Run Python syntax checks:

   ```powershell
   @'
   from pathlib import Path
   import py_compile
   files = [Path("light_field_plugin/__init__.py")]
   for folder in ("core", "operators", "panels", "properties"):
       files.extend(Path("light_field_plugin", folder).glob("*.py"))
   for path in files:
       py_compile.compile(str(path), doraise=True)
   '@ | python -
   ```

3. Run unit tests:

   ```powershell
   python -m unittest discover -s tests -v
   ```

4. Run Blender integration tests if Blender is installed:

   ```powershell
   blender --background --python scripts\blender_integration_test.py
   ```

5. Build the add-on ZIP:

   ```powershell
   .\scripts\build_release.ps1
   ```

6. Install the generated ZIP in Blender.
7. Confirm the add-on enables successfully.
8. Create a small camera array and render PNG, continuous TIFF, and 1-bit Film TIFF outputs.
9. Generate current-frame final delivery output and verify `interlaced_preview.png`, `film_1bit.tif`, and `delivery_manifest.json`. If `输出连续调 interlaced.tif` is enabled, also verify `interlaced.tif`.

## GitHub Release

Recommended tag format:

```text
v0.1.11
```

Recommended title:

```text
Light Field Render v0.1.11
```

Release asset:

```text
dist/light_field_render-v0.1.11.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.11

Native large-delivery acceleration and fast film-output release.

### Features

- Creates a configurable linear off-axis light-field camera array.
- Visualizes focal plane and display depth volume.
- Supports single-frame rendering across all cameras.
- Supports animation rendering across all cameras and a selected frame range.
- Includes resumable rendering based on existing output files.
- Defers heavy camera-array updates while sliders are dragged to avoid UI stalls.
- Adds PNG, continuous TIFF, and halftoned 1-bit Film TIFF output modes.
- Adds FM error-diffusion and AM clustered-dot halftone controls.
- Adds automated unit tests and Blender background integration test coverage.
- Refreshes focal-plane and depth-box helper visuals when output resolution settings are applied.
- Localizes the Blender sidebar panels, property labels, operators, and status messages to Chinese.
- Adds `最终交付输出` for current-frame delivery from physical size in mm plus PPI.
- Generates 2048px preview PNG, single-channel 1-bit film TIFF, and JSON manifest by default.
- Adds optional `输出连续调 interlaced.tif` output for workflows that need the full continuous-tone RGB interlaced TIFF.
- Keeps Blender source-view resolution separate from final delivery pixel size to avoid rendering every camera at print resolution.
- Reuses or renders current-frame source PNG views before interlacing.
- Adds large-output confirmation, source-upscale warning, progress/status reporting, and error-log output.
- Automatically writes BigTIFF when continuous interlaced RGB output exceeds classic TIFF 32-bit limits.
- Adds native Windows acceleration for same-dimension, zero-degree AM delivery generation.
- Adds optional NumPy acceleration and bundles it in release ZIPs by default.
- Speeds up RGB/filter-0 PNG source loading and same-dimension source-view row sampling.
- Bundles Blender-compatible NumPy and the Windows native accelerator in release ZIPs by default for no-install user setup.
- Adds Blender status-bar progress updates for rendering and delivery generation.
- Stress-tested `194 x 345 mm @ 4000 PPI` with 150 source views at `2160 x 3651`: fast film mode generated `film_1bit.tif`, preview, and manifest in about 10.2 seconds on the test workstation.
- With optional continuous-tone `interlaced.tif` enabled, the same stress test generated the 4.98 GB BigTIFF plus 1-bit TIFF and preview in about 32.2 seconds; the remaining cost is dominated by writing the 4.98 GB RGB TIFF.

### Installation

Download `light_field_render-v0.1.11.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
