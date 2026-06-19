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
v0.1.17
```

Recommended title:

```text
Light Field Render v0.1.17
```

Release asset:

```text
dist/light_field_render-v0.1.17.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.17

AM diamond LBY-like print TIFF fitting.

### Features

- Changes `LBY-like近似` from the previous FM-like stochastic screen to a deterministic AM diamond clustered-dot screen fitted from the available 150 JPG -> 1-bit TIFF factory pair.
- Keeps direct native Windows WIC decoding for JPG source views in the background delivery worker, avoiding slow UI-thread JPG conversion.
- Confirms delivery interlace remains whole-pixel PE interlace, not RGB subpixel interlace.
- Full-size direct-JPG test with 150 source views at `2160 x 3651`, final `30551 x 54342`, `4000 PPI`, `PE=52.64`, reversed views: total generation time about `12.5s`.
- Full-size comparison against `618空间_dats_dats.tif`: mismatch about `10.67%`, generated black ratio about `11.71%`, target black ratio about `15.31%`.
- Uses AM diamond parameters recorded in `delivery_manifest.json`: `65 LPI`, `75°`, `gamma=2.0`, `density_scale=2.5`, phase X `20.512820512820515`, phase Y `41.02564102564103`.
- Leaves `LBY-like近似` explicitly documented as an approximation, not a bitwise clone of the factory RIP.
- Defaults source-view output to JPG quality 95 while retaining PNG and continuous TIFF options.
- Forces Standard color management while writing JPG source views, then restores the scene settings.
- Reads disk JPG source views for final delivery so factory handoff inputs and plugin inputs match.
- Keeps final delivery interlacing as whole-pixel view selection.
- Keeps `只生成连续调交织图` for interlaced-only output without `film_1bit.tif`.
- Writes `film_1bit.tif` as uncompressed 1-bit TIFF with `PhotometricInterpretation=1` (`0=black`, `1=white`).
- Adds local real-pair analysis tooling under `scripts/analyze_lby_pair.py` without committing the large sample images.

### Installation

Download `light_field_render-v0.1.17.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
