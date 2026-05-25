# Release Checklist

Use this checklist when publishing a GitHub Release.

## Preflight

1. Confirm `light_field_plugin/__init__.py` has the intended `bl_info["version"]`.
2. Run Python syntax checks:

   ```powershell
   python -m py_compile light_field_plugin\__init__.py light_field_plugin\core\*.py light_field_plugin\operators\*.py light_field_plugin\panels\*.py light_field_plugin\properties\*.py
   ```

3. Build the add-on ZIP:

   ```powershell
   .\scripts\build_release.ps1
   ```

4. Install the generated ZIP in Blender.
5. Confirm the add-on enables successfully.
6. Create a small camera array and render a low-resolution test frame.

## GitHub Release

Recommended tag format:

```text
v0.1.0
```

Recommended title:

```text
Light Field Render v0.1.0
```

Release asset:

```text
dist/light_field_render-v0.1.0.zip
```

Suggested release notes:

```markdown
## Light Field Render v0.1.0

Initial Blender add-on preview release.

### Features

- Creates a configurable linear off-axis light-field camera array.
- Visualizes focal plane and display depth volume.
- Supports single-frame rendering across all cameras.
- Supports animation rendering across all cameras and a selected frame range.
- Includes resumable rendering based on existing output files.

### Installation

Download `light_field_render-v0.1.0.zip`, then install it from Blender via `Edit > Preferences > Add-ons > Install...`.
```
