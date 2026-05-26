# Light Field Render User Manual

This manual describes how to install and use the `Light Field Render` Blender add-on.

## 1. Installation

Download the add-on ZIP from the GitHub Release page. The file should be named like:

```text
light_field_render-v0.1.6.zip
```

Install it in Blender:

1. Open Blender 4.2 LTS or newer.
2. Open `Edit > Preferences > Add-ons`.
3. Click `Install...`.
4. Select the ZIP file.
5. Enable `Light Field Render`.
6. Open the 3D Viewport sidebar with `N`.
7. Use the `Light Field` tab.

## 2. Main Concepts

The add-on creates a row of cameras for light-field rendering. All cameras are arranged linearly and use off-axis projection so that their frustums converge on a shared focal plane.

Important terms:

- `Camera Count`: total number of views in the light-field camera array.
- `Focal Distance`: distance from the camera array to the focal plane.
- `Opening Angle`: angular coverage of the camera array.
- `Depth Range`: visual depth volume around the focal plane.
- `Focal Length`: Blender camera lens focal length in millimeters.
- `Sensor Width`: Blender camera sensor width in millimeters.
- `Resolution`: output resolution for each rendered view.
- `Output Format`: `PNG`, continuous `TIFF`, or halftoned `1-bit Film TIFF`.

## 3. Creating A Camera Array

1. Open the `Light Field` tab in the 3D Viewport sidebar.
2. Set the physical geometry parameters.
3. Set camera intrinsics.
4. Click `Create Light Field Camera`.

After the camera system exists, slider edits are intentionally lightweight. They mark the camera array as changed, but they do not rebuild or update every camera on every mouse movement. Click `Apply Camera Parameters` to apply the pending camera geometry and intrinsic changes. This avoids UI stalls while dragging sliders such as focal length, focal distance, opening angle, or camera count.

The add-on creates:

- A `LightField_Control` empty object.
- A focal-plane helper object.
- A display-depth helper object.
- A camera array named with the `LF_Camera_` prefix.

The helper objects are visualization aids and are not intended to be rendered.

## 4. Previewing Views

After the camera system is created, use the preview controls in the add-on panel:

- Select an active camera index.
- Jump to first or last camera.
- Step to previous or next camera.

The active camera becomes the scene camera.

## 5. Rendering A Single Frame

1. Set the scene to the target frame.
2. Set the output directory.
3. Set output resolution.
4. Select output format.
5. Click the single-frame render button.

The add-on renders all cameras for the current frame. Output is grouped by frame:

```text
light_field_output/
  frame_0001/
    camera_000.png
    camera_001.png
    ...
```

If rendering is interrupted, the add-on checks existing output files and resumes from the first missing camera image.

The resume check is format-aware. For `1-bit Film TIFF`, an existing `.tif` is only treated as complete if it is actually a 1-bit TIFF, so a previous continuous TIFF render will not be mistaken for a finished film output.

## 6. Rendering Animation

1. Set `Frame Start` and `Frame End` in the add-on panel.
2. Set the output directory.
3. Click the animation render button.

The add-on renders the selected frame range for each camera. Output is grouped by camera:

```text
light_field_output/
  camera_000/
    frame_0001.png
    frame_0002.png
    ...
  camera_001/
    frame_0001.png
    frame_0002.png
    ...
```

## 7. Output Formats

The add-on supports three output modes:

- `PNG`: standard continuous-tone PNG image output.
- `TIFF`: standard continuous-tone TIFF output written by Blender.
- `1-bit Film TIFF`: renders a temporary continuous PNG source, converts it to a black/white halftoned 1-bit TIFF, and deletes the temporary source unless `Keep Continuous Source` is enabled.

For `1-bit Film TIFF`, the output is a single-channel 1-bit TIFF intended for film/RIP workflows that need pure black/white dots instead of continuous tone. The TIFF writer stores `BitsPerSample=1`, `SamplesPerPixel=1`, `Compression=none`, and inch-based DPI metadata.

Halftone controls:

- `FM / Error Diffusion`: dispersed fixed-size dots. This is the default because it is generally less prone to visible moire in lenticular/light-field workflows.
- `AM / Clustered Dot`: traditional clustered dots controlled by `DPI`, `LPI`, screen angle, and dot shape.
- `DPI`: output resolution metadata and AM cell-size basis.
- `LPI`: AM screen ruling, used only in AM mode.
- `Screen Angle`: AM screen angle in degrees.
- `Dot Shape`: round, diamond, or ellipse in AM mode.
- `Gamma`: luminance correction before halftoning.

## 8. Recommended Workflow

1. Test with a small camera count such as `5` or `9`.
2. Verify that the focal plane and depth helper volume match the target scene.
3. Render a low-resolution single frame.
4. Check left, center, and right views.
5. If film output is required, test `1-bit Film TIFF` at low resolution and inspect dot density.
6. Increase camera count and resolution.
7. Render the final frame or animation.

## 9. Troubleshooting

If the add-on panel is not visible:

- Confirm the add-on is enabled in Blender preferences.
- Open the 3D Viewport sidebar with `N`.
- Look for the `Light Field` tab.

If rendering does not start:

- Create the light-field camera system first.
- Check that the output path is valid.
- Confirm that another render task is not already running.

If output files are incomplete:

- Re-run the same render command.
- The add-on will resume from the first missing camera output.

If the active camera does not change:

- Confirm the camera array exists.
- Recreate the light-field camera system if Blender undo/redo removed helper objects.

If changing width or height appears not to affect Blender scene settings:

- Click `Apply Output Settings`, or start a render. The render operators always apply `Width` and `Height` before rendering.
- Width and height are stored independently and are no longer coupled through a live update callback.

If slider dragging feels delayed:

- This is intentional. Heavy camera-array updates are deferred. Click `Apply Camera Parameters`, or enable `Auto Apply After Drag` if you want delayed automatic application.

## 10. Notes For Release Builds

The Blender add-on ZIP must contain the add-on package folder at the ZIP root:

```text
light_field_plugin/
  __init__.py
  core/
  operators/
  panels/
  properties/
```

Use `scripts/build_release.ps1` to create this layout automatically.
