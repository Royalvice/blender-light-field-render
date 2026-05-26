# Release Checklist

Use this checklist when publishing a GitHub Release.

## Preflight

1. Confirm `light_field_plugin/__init__.py` has the intended `bl_info["version"]`.
2. Run Python syntax checks:

   ```powershell
   python -m py_compile light_field_plugin\__init__.py light_field_plugin\core\*.py light_field_plugin\operators\*.py light_field_plugin\panels\*.py light_field_plugin\properties\*.py
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

## GitHub Release

Recommended tag format:

```text
v0.1.6
```

Recommended title:

```text
Light Field Render v0.1.6
```

Release asset:

```text
dist/light_field_render-v0.1.6.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.6

Output settings refresh release.

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

### Installation

Download `light_field_render-v0.1.6.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
