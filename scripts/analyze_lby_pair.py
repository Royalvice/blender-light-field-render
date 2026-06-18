"""Analyze local LBY input/output pairs without committing large assets.

This script is intentionally a developer/probe tool, not a Blender add-on
runtime dependency. It can use Pillow/tifffile/numpy from the local Python
environment.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import tifffile
from PIL import Image


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_paths(input_dir: Path) -> list[Path]:
    return sorted(
        [path for path in input_dir.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg"}],
        key=lambda path: path.name.lower(),
    )


def inspect_inputs(input_dir: Path) -> dict:
    paths = image_paths(input_dir)
    dimensions: dict[str, int] = {}
    modes: dict[str, int] = {}
    samples = []
    for index, path in enumerate(paths):
        with Image.open(path) as image:
            key = f"{image.width}x{image.height}"
            dimensions[key] = dimensions.get(key, 0) + 1
            modes[image.mode] = modes.get(image.mode, 0) + 1
            if index < 5 or index >= max(0, len(paths) - 5):
                samples.append(
                    {
                        "index": index,
                        "name": path.name,
                        "size": [image.width, image.height],
                        "mode": image.mode,
                        "sha256": sha256(path),
                    }
                )
    return {
        "directory": str(input_dir),
        "count": len(paths),
        "dimensions": dimensions,
        "modes": modes,
        "samples": samples,
    }


def inspect_tiff(path: Path) -> dict:
    with tifffile.TiffFile(path) as tif:
        page = tif.pages[0]
        array = page.asarray()
        sample = array[: min(2048, array.shape[0]), : min(2048, array.shape[1])]
        tags = page.tags
        return {
            "path": str(path),
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
            "shape": list(array.shape),
            "dtype": str(array.dtype),
            "unique_values_sample": [int(value) for value in np.unique(sample)],
            "black_ratio_sample": float(np.mean(sample == 0)),
            "tags": {
                "ImageWidth": int(tags["ImageWidth"].value),
                "ImageLength": int(tags["ImageLength"].value),
                "BitsPerSample": int(tags["BitsPerSample"].value),
                "Compression": int(tags["Compression"].value),
                "PhotometricInterpretation": int(tags["PhotometricInterpretation"].value),
                "SamplesPerPixel": int(tags["SamplesPerPixel"].value),
                "XResolution": tuple(int(value) for value in tags["XResolution"].value),
                "YResolution": tuple(int(value) for value in tags["YResolution"].value),
                "ResolutionUnit": int(tags["ResolutionUnit"].value),
            },
        }


def compare_tiffs(generated: Path, target: Path) -> dict:
    with tifffile.TiffFile(generated) as gen_tif, tifffile.TiffFile(target) as target_tif:
        generated_array = gen_tif.pages[0].asarray()
        target_array = target_tif.pages[0].asarray()
    if generated_array.shape != target_array.shape:
        return {
            "same_shape": False,
            "generated_shape": list(generated_array.shape),
            "target_shape": list(target_array.shape),
        }
    mismatch = np.not_equal(generated_array, target_array)
    mismatch_count = int(np.count_nonzero(mismatch))
    total = int(mismatch.size)
    return {
        "same_shape": True,
        "mismatch_count": mismatch_count,
        "total_pixels": total,
        "mismatch_ratio": mismatch_count / total if total else 0.0,
        "black_ratio_generated": float(np.mean(generated_array == 0)),
        "black_ratio_target": float(np.mean(target_array == 0)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, help="Directory containing extracted camera_*.jpg files.")
    parser.add_argument("--target-tiff", type=Path, required=True)
    parser.add_argument("--generated-tiff", type=Path)
    parser.add_argument("--output", type=Path, default=Path("lby_pair_report.json"))
    args = parser.parse_args()

    report = {"target_tiff": inspect_tiff(args.target_tiff)}
    if args.input_dir:
        report["input_images"] = inspect_inputs(args.input_dir)
    if args.generated_tiff:
        report["comparison"] = compare_tiffs(args.generated_tiff, args.target_tiff)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
