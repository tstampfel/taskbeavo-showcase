#!/usr/bin/env python3
"""Check the demo screenshots in docs/demo against the size rule in docs/demo/README.md.

Every PNG in docs/demo/web must be 800 x 1720 and every PNG in docs/demo/mobile 603 x 1311,
so the README rows line up. Each file may hold only the IHDR, IDAT and IEND chunks: no text,
time, EXIF or colour-profile chunks that could carry metadata.

Exits non-zero and prints the offending file names otherwise.
Usage: python3 scripts/check_demo_screenshots.py [DEMO_DIR]
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

SIZES = {"web": (800, 1720), "mobile": (603, 1311)}
ALLOWED_CHUNKS = {b"IHDR", b"IDAT", b"IEND"}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    """Return the (type, body) pairs of a PNG file, or raise ValueError if it isn't one."""
    if not data.startswith(PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    chunks = []
    offset = len(PNG_SIGNATURE)
    while offset < len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        kind = data[offset + 4 : offset + 8]
        chunks.append((kind, data[offset + 8 : offset + 8 + length]))
        offset += 12 + length  # length, type, body, CRC
    return chunks


def problems(path: Path, expected: tuple[int, int]) -> list[str]:
    try:
        chunks = png_chunks(path.read_bytes())
    except ValueError as error:
        return [str(error)]
    found = []
    width, height = struct.unpack(">II", chunks[0][1][:8])
    if (width, height) != expected:
        found.append(f"{width}x{height}, expected {expected[0]}x{expected[1]}")
    extra = sorted({kind.decode("latin-1") for kind, _ in chunks if kind not in ALLOWED_CHUNKS})
    if extra:
        found.append(f"metadata chunks {', '.join(extra)}")
    return found


def main(argv: list[str]) -> int:
    default_dir = Path(__file__).resolve().parent.parent / "docs" / "demo"
    demo_dir = Path(argv[1]) if len(argv) > 1 else default_dir
    failed = False
    for folder, expected in SIZES.items():
        images = sorted((demo_dir / folder).glob("*.png"))
        if not images:
            print(f"FAIL {folder}: no screenshots found in {demo_dir / folder}")
            failed = True
        for image in images:
            for problem in problems(image, expected):
                print(f"FAIL {folder}/{image.name}: {problem}")
                failed = True
    if failed:
        print("See the screenshot rule in docs/demo/README.md.")
        return 1
    print("PASS: every demo screenshot has its set's size and no metadata")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
