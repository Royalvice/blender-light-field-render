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
v0.1.16
```

Recommended title:

```text
Light Field Render v0.1.16
```

Release asset:

```text
dist/light_field_render-v0.1.16.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.16

Native JPG loading and FM-like print TIFF fix.

### Features

- Fixes slow and potentially freezing JPG source loading by decoding JPG directly with native Windows WIC in the background delivery worker.
- Changes `LBY-like近似` from single-threshold line-art output to a deterministic FM-like dot screen, so `film_1bit.tif` contains real 1-bit dots instead of a mostly white image with a few black lines.
- Confirms delivery interlace remains whole-pixel PE interlace, not RGB subpixel interlace.
- Full-size direct-JPG test with 150 source views at `2160 x 3651`, final `30551 x 54342`, `4000 PPI`, `PE=52.64`: total generation time about `14.77s`.
- Defaults source-view output to JPG quality 95 while retaining PNG and continuous TIFF options.
- Forces Standard color management while writing JPG source views, then restores the scene settings.
- Reads disk JPG source views for final delivery so factory handoff inputs and plugin inputs match.
- Changes final delivery interlacing to whole-pixel view selection instead of RGB subpixel view selection.
- Keeps `只生成连续调交织图` for interlaced-only output without `film_1bit.tif`.
- Replaces exposed final print algorithms with `LBY-like近似`.
- Writes `film_1bit.tif` as uncompressed 1-bit TIFF with `PhotometricInterpretation=1` (`0=black`, `1=white`).
- Records the LBY-like FM screen approximation note in `delivery_manifest.json`.
- Adds local real-pair analysis tooling under `scripts/analyze_lby_pair.py` without committing the large sample images.
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
- Adds `只生成连续调交织图`, which writes only `interlaced.tif`, `interlaced_preview.png`, and `delivery_manifest.json`; it skips halftoning and does not output `film_1bit.tif`.
- Keeps Blender source-view resolution separate from final delivery pixel size to avoid rendering every camera at print resolution.
- Reuses or renders current-frame source PNG views before interlacing.
- Changing halftone or interlace settings no longer forces all source PNG views to rerender; existing matching PNGs are reused for delivery generation.
- Interactive delivery generation now runs as a modal operation: source rendering happens one camera at a time, and interlace/halftone/TIFF generation runs on a background worker so the UI can process `停止交付生成`.
- Adds large-output confirmation, source-upscale warning, progress/status reporting, and error-log output.
- Automatically writes BigTIFF when continuous interlaced RGB output exceeds classic TIFF 32-bit limits.
- Adds native Windows acceleration for same-dimension, zero-degree AM delivery generation.
- Native acceleration can also generate RGB-only interlaced TIFF batches without computing 1-bit halftone rows.
- Native acceleration uses Windows system threads and depends only on `KERNEL32.dll`; it does not require Visual Studio, OpenMP, or `VCOMP140.DLL` on user machines.
- Adds optional NumPy acceleration and bundles it in release ZIPs by default.
- Speeds up RGB/filter-0 PNG source loading and same-dimension source-view row sampling.
- Bundles Blender-compatible NumPy and the Windows native accelerator in release ZIPs by default for no-install user setup.
- Adds Blender status-bar progress updates for rendering and delivery generation.
- Defaults final film halftone to AM so zero-degree large delivery can use the native fast path.
- Adds UI warnings when FM or non-zero interlace angle prevents the native fast path.
- Stress-tested `194 x 345 mm @ 4000 PPI` with 150 source views at `2160 x 3651`: fast film mode generated `film_1bit.tif`, preview, and manifest in about 9.8 seconds on the test workstation.
- Verified `停止交付生成` during a slow FM delivery: UI accepted the stop request, temporary `.tmp` files were removed, `delivery_error.log` was written, and Blender state was restored.
- Verified interlaced-only output removes stale `film_1bit.tif` and records `"film_1bit_tiff": null` in the manifest.
- With optional continuous-tone `interlaced.tif` enabled, the same stress test generated the 4.98 GB BigTIFF plus 1-bit TIFF and preview in about 32.2 seconds; the remaining cost is dominated by writing the 4.98 GB RGB TIFF.

### Installation

Download `light_field_render-v0.1.16.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
