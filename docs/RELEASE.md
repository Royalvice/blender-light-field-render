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
   blender --background --factory-startup --python scripts\blender_integration_test.py
   ```

5. Build the add-on ZIP:

   ```powershell
   .\scripts\build_release.ps1
   ```

6. Install the generated ZIP in Blender.
7. Confirm the add-on enables successfully.
8. Create a small camera array and render JPG source views, continuous interlaced TIFF, and 1-bit Film TIFF outputs.
9. Generate current-frame final delivery output and verify `interlaced_preview.png`, `film_1bit.tif`, and `delivery_manifest.json`. If continuous interlaced TIFF output is enabled, also verify `interlaced.tif`.
10. For LBY validation releases, compare the generated 1-bit TIFF against the vendor target with the target-active mask metric before publishing.

## GitHub Release

Recommended tag format:

```text
v0.1.19
```

Recommended title:

```text
Light Field Render v0.1.19
```

Release asset:

```text
dist/light_field_render-v0.1.19.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.19

Replaces the previous AM diamond LBY approximation with `LBY_row_threshold_v1`, a deterministic 18 px horizontal row-threshold screen fitted against the available factory TIFF target.

### Features

- Final delivery exposes only the `LBY 行阈值屏` print algorithm in the UI.
- The fixed profile is `LBY_row_threshold_v1`: period `18 px`, Y phase `0`, gamma `0.25`, density `0.25`, bias `-0.05`, and a fixed 18-entry row threshold table.
- Keeps whole-pixel PE interlacing, JPG source loading through the native Windows decoder, and uncompressed 1-bit TIFF output with `PhotometricInterpretation=1`.
- Keeps separated delivery operations: generate final delivery in one command, generate only the continuous interlaced TIFF, or halftone an existing `interlaced.tif`.
- Full-size direct native generation from 150 JPG views at `2160 x 3651`, final `30551 x 54342`, `4000 PPI`, `PE=52.64`, reversed views: `18.55s` generation time on the validation workstation.
- Full-size comparison against `618空间_dats_dats.tif`: global mismatch `1.6745%`; target-active mismatch `3.7562%` using a 32 px dilated target-black mask.
- Generated black ratio is about `15.55%`; target black ratio is about `15.31%`.

### Installation

Download `light_field_render-v0.1.19.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
