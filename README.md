# Light Field Render for Blender

Light Field Render is a Blender add-on for creating and rendering a linear off-axis light-field camera array. It is intended for light-field display content generation, multi-view rendering, and camera-array visualization inside Blender.

The repository is now organized around the Blender add-on. The older Three.js visualizer is preserved under `archive/web_viz/` for reference, but it is no longer the primary project entry point.

## Features

- Creates a configurable linear light-field camera array.
- Uses Blender camera `shift_x` to implement off-axis projection.
- Visualizes the focal plane and display depth volume with non-rendered helper objects.
- Supports single-frame rendering across all cameras.
- Supports animation rendering across all cameras and a selected frame range.
- Supports PNG, continuous TIFF, and halftoned 1-bit Film TIFF output.
- Avoids UI stalls by deferring heavy camera-array updates while sliders are dragged.
- Tracks render progress and can resume from existing output files.

## Requirements

- Blender 4.2 LTS or newer.
- No third-party Python packages are required for the Blender add-on.

## Install

Use the release ZIP asset named like:

```text
light_field_render-v0.1.6.zip
```

Then install it in Blender:

1. Open Blender.
2. Go to `Edit > Preferences > Add-ons`.
3. Click `Install...`.
4. Select the release ZIP file.
5. Enable `Light Field Render`.
6. Open the 3D Viewport sidebar and use the `Light Field` tab.

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
3. Select the `Light Field` tab.
4. Set camera count, focal plane distance, opening angle, focal length, and sensor width.
5. Click `Create Light Field Camera`.
6. If you change camera parameters after creation, click `Apply Camera Parameters`.
7. Preview cameras with the active camera controls.
8. Set an output directory and output format.
9. Run single-frame or animation rendering.

See [docs/USER_MANUAL.md](docs/USER_MANUAL.md) for the full workflow.

## Build Release ZIP

From the repository root:

```powershell
.\scripts\build_release.ps1
```

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
