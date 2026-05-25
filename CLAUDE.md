# Repository Instructions

This repository is centered on the Blender add-on in `light_field_plugin/`.

## Project Structure

- `light_field_plugin/` is the primary deliverable and should remain installable as a Blender add-on package.
- `archive/web_viz/` contains the archived Three.js visualizer. Treat it as reference material unless explicitly asked to update it.
- `docs/` contains user-facing manuals, technical notes, and release guidance.
- `scripts/build_release.ps1` builds the Blender add-on ZIP under `dist/`.
- `utils/` contains auxiliary light-field image utilities and sample data.

## Development Rules

- Keep the Blender add-on compatible with Blender 4.2 LTS unless a task explicitly changes the target version.
- Do not add third-party Python dependencies to the add-on without documenting installation and release impact.
- Preserve the add-on package layout expected by Blender:

  ```text
  light_field_plugin/
    __init__.py
    core/
    operators/
    panels/
    properties/
  ```

- When changing add-on behavior, run Python syntax checks before committing:

  ```powershell
  python -m py_compile light_field_plugin\__init__.py light_field_plugin\core\*.py light_field_plugin\operators\*.py light_field_plugin\panels\*.py light_field_plugin\properties\*.py
  ```

- When changing release packaging, rebuild the add-on ZIP:

  ```powershell
  .\scripts\build_release.ps1
  ```

## Release Rules

- Keep `bl_info["version"]` in `light_field_plugin/__init__.py` aligned with release tags.
- Release tags should use `vMAJOR.MINOR.PATCH`, for example `v0.1.0`.
- The release ZIP must contain `light_field_plugin/` at the ZIP root.
- Do not commit `dist/` artifacts; upload ZIP files as GitHub Release assets.
