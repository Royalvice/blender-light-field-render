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
v0.1.18
```

Recommended title:

```text
Light Field Render v0.1.18
```

Release asset:

```text
dist/light_field_render-v0.1.18.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.18

Separated profile-based halftone workflow and calibration reports.

### Features

- Adds explicit `LBY_approx_am_diamond_v1` halftone profile metadata for the LBY-like AM diamond screen.
- Adds `从交织图生成菲林 TIFF`: reads an existing add-on generated `interlaced.tif`, applies the fixed profile, and writes `film_1bit.tif` without rerendering or reinterlacing.
- Adds `halftone_calibration_report.json` with input TIFF metadata, profile parameters, output black ratio, elapsed time, and optional target comparison.
- Adds streaming 1-bit TIFF comparison for vendor targets, reporting shape, mismatch count/ratio, generated black ratio, and target black ratio without loading the whole image into memory.
- Keeps the hard production boundary: unsupported TIFF compression/layouts fail explicitly instead of falling back silently.
- Keeps direct delivery generation, JPG source loading, whole-pixel PE interlace, and the native fast path from `v0.1.17`.
- Full-size direct-JPG test with 150 source views at `2160 x 3651`, final `30551 x 54342`, `4000 PPI`, `PE=52.64`, reversed views: total generation time about `12.5s`.
- Full-size comparison against `618空间_dats_dats.tif`: mismatch about `10.67%`, generated black ratio about `11.71%`, target black ratio about `15.31%`.

### Installation

Download `light_field_render-v0.1.18.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
