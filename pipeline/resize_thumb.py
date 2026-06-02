#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow"]
# ///
"""Downscale a raster image IN PLACE to a maximum width.

Usage:
    python3 resize_thumb.py <image-path> [max-width]

Used by enrich.py / enrich_gate.sh to keep card thumbnails small. No-op for
images already <= max width, for SVGs, or for anything Pillow can't open
(exit 0, non-fatal — the caller treats resize as best-effort).
"""
from __future__ import annotations

import sys
from pathlib import Path

DEFAULT_MAX_WIDTH = 720


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: resize_thumb.py <image-path> [max-width]", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    max_w = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_MAX_WIDTH
    if not path.exists() or path.suffix.lower() == ".svg":
        return 0
    try:
        from PIL import Image
    except Exception:
        return 0  # Pillow missing -> leave original untouched, non-fatal
    try:
        with Image.open(path) as im:
            fmt = im.format
            w, h = im.size
            if w <= max_w:
                return 0
            new_h = max(1, round(h * max_w / w))
            if im.mode not in ("RGB", "RGBA", "L"):
                im = im.convert("RGB")
            resized = im.resize((max_w, new_h), Image.LANCZOS)
            resized.save(path, format=fmt or "PNG")
    except Exception:
        return 0  # corrupt/animated/etc. -> non-fatal, keep original
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
