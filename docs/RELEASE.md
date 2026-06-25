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
10. For standard AM releases, generate the QA sample and inspect the PNG/TIFF for smooth grayscale ramps:

   ```powershell
   python scripts\generate_standard_am_sample.py --output-dir standard_am_sample
   ```

11. For LBY validation releases, compare the generated 1-bit TIFF against the vendor target with the target-active mask metric before publishing.

## GitHub Release

Recommended tag format:

```text
v0.1.24
```

Recommended title:

```text
Light Field Render v0.1.24
```

Release asset:

```text
dist/light_field_render-v0.1.24.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.24

Switches the default 1-bit film output to a standard clustered-dot AM screen for smoother grayscale ramps, while keeping LBY profiles available for comparison and reverse-engineering work.

### Features

- Adds `标准AM菲林` as the default print TIFF algorithm: `4000 PPI`, `200 LPI`, `45°`, round clustered dots, Rec.709 luma, and linear `Gamma=1.0`.
- Keeps `LBY v1 行阈值屏` and `LBY v2 探针反推` as explicit fallback/comparison algorithms.
- Shows AM parameters and LBY parameters separately in the UI, so inactive LPI/line-screen controls no longer look applicable to every algorithm.
- Disables stale LBY tuning variants for AM output and cleans old variant files when AM is selected.
- Makes the standalone `从交织图生成菲林 TIFF` operation respect the selected halftone method instead of forcing LBY.
- Adds `scripts/generate_standard_am_sample.py` for quick PNG + 1-bit TIFF grayscale ramp QA samples.
- Adds a cross-platform Python release builder for macOS/Linux environments where PowerShell is unavailable.

### Installation

Download `light_field_render-v0.1.24.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
