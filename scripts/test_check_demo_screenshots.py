#!/usr/bin/env python3
"""Tests for check_demo_screenshots.py. Run: python3 scripts/test_check_demo_screenshots.py"""

from __future__ import annotations

import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_demo_screenshots as check


def chunk(kind: bytes, body: bytes) -> bytes:
    return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))


def png(width: int, height: int, extra: list[tuple[bytes, bytes]] | None = None) -> bytes:
    """A real (if blank) greyscale PNG of the given size, with optional extra chunks."""
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    rows = b"".join(b"\x00" + b"\xff" * width for _ in range(height))
    chunks = [chunk(b"IHDR", header)]
    chunks += [chunk(kind, body) for kind, body in extra or []]
    chunks += [chunk(b"IDAT", zlib.compress(rows)), chunk(b"IEND", b"")]
    return check.PNG_SIGNATURE + b"".join(chunks)


class CheckDemoScreenshotsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "web").mkdir()
        (self.root / "mobile").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, name: str, data: bytes) -> None:
        (self.root / name).write_bytes(data)

    def run_check(self) -> int:
        return check.main(["check", str(self.root)])

    def test_passes_when_every_set_has_its_size(self) -> None:
        self.write("web/01.png", png(800, 1720))
        self.write("mobile/01.png", png(603, 1311))
        self.assertEqual(self.run_check(), 0)

    def test_fails_on_a_full_page_web_capture(self) -> None:
        self.write("web/01.png", png(800, 1720))
        self.write("web/02.png", png(800, 3270))
        self.write("mobile/01.png", png(603, 1311))
        self.assertEqual(self.run_check(), 1)

    def test_fails_on_a_mobile_image_of_another_size(self) -> None:
        self.write("web/01.png", png(800, 1720))
        self.write("mobile/01.png", png(1206, 2622))
        self.assertEqual(self.run_check(), 1)

    def test_fails_on_a_text_metadata_chunk(self) -> None:
        self.write("web/01.png", png(800, 1720, [(b"tEXt", b"Author\x00someone")]))
        self.write("mobile/01.png", png(603, 1311))
        self.assertEqual(self.run_check(), 1)

    def test_fails_on_a_file_that_is_not_a_png(self) -> None:
        self.write("web/01.png", b"not a png")
        self.write("mobile/01.png", png(603, 1311))
        self.assertEqual(self.run_check(), 1)

    def test_fails_when_a_set_is_empty(self) -> None:
        self.write("web/01.png", png(800, 1720))
        self.assertEqual(self.run_check(), 1)

    def test_the_committed_screenshots_pass(self) -> None:
        self.assertEqual(check.main(["check"]), 0)


if __name__ == "__main__":
    unittest.main()
