"""Package reviewed image masters; image generation is an offline authoring step."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from skoleklar.weekend_art import PRESETS, SOURCE_SIZE


def build_assets(root: Path) -> None:
    from PIL import Image, ImageOps

    masters = root / "imgs/house"
    provenance = masters / "generation.json"
    generation = json.loads(provenance.read_text(encoding="utf-8"))
    if set(generation["prompts"]) != set(PRESETS):
        raise ValueError("generation.json must record a prompt for each artwork preset")
    # Validate and encode every source before replacing any packaged asset.
    encoded = {}
    hashes = {}
    import io

    for preset in PRESETS:
        path = masters / f"{preset}.png"
        with Image.open(path) as master:
            master.load()
            if master.width < SOURCE_SIZE[0] or master.height < SOURCE_SIZE[1]:
                raise ValueError(f"{preset}: the master must be at least 680 by 400 pixels")
            image = ImageOps.fit(ImageOps.exif_transpose(master).convert("RGB"), SOURCE_SIZE,
                                 Image.Resampling.LANCZOS, centering=(0.5, 0.35))
            output = io.BytesIO()
            image.save(output, format="WEBP", quality=88, method=6)
            encoded[preset] = output.getvalue()
        hashes[preset] = hashlib.sha256(path.read_bytes()).hexdigest()
    output = root / "src/skoleklar/artwork"
    output.mkdir(parents=True, exist_ok=True)
    for preset, data in encoded.items():
        (output / f"{preset}.webp").write_bytes(data)
    (output / "sources.json").write_text(json.dumps({
        "size": list(SOURCE_SIZE), "crop_focus": [0.5, 0.35],
        "sources": {preset: f"imgs/house/{preset}.png" for preset in PRESETS},
        "source_sha256": hashes, "generation": "imgs/house/generation.json",
        "conversion": "WebP quality 88; e-ink resized to 340x200 before six-pigment diffusion",
    }, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="Repository root (default: current folder)")
    args = parser.parse_args()
    try:
        build_assets(args.root.resolve())
    except (OSError, ValueError, KeyError) as error:
        parser.error(str(error))
    print(f"Packaged {len(PRESETS)} house images")


if __name__ == "__main__":
    main()
