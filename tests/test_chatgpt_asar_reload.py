"""Synthetic-only tests for opt-in ChatGPT ASAR patch utility.

No actual ChatGPT installation, binaries, credentials, or network needed.
"""
from __future__ import annotations

import hashlib
import json
import runpy
import struct
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
patch = runpy.run_path(str(REPO / "scripts" / "chatgpt_asar_reload.py"))
recon = runpy.run_path(str(REPO / "scripts" / "chatgpt_asar_recon.py"))
OLD_GATE = patch["OLD_GATE"]
NEW_GATE = patch["NEW_GATE"]
OLD_KEY = patch["OLD_KEY"]
NEW_KEY = patch["NEW_KEY"]
PatchError = patch["PatchError"]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fixture(code: bytes) -> tuple[bytes, dict]:
    block_size = 32
    integrity = {
        "algorithm": "SHA256",
        "hash": digest(code),
        "blockSize": block_size,
        "blocks": [
            digest(code[i:i + block_size]) for i in range(0, len(code), block_size)
        ],
    }
    node = {"offset": "0", "size": len(code), "integrity": integrity}
    path = patch["ENTRY"].split("/")
    children = {path[-1]: node}
    for part in reversed(path[:-1]):
        children = {part: {"files": children}}
    return json.dumps({"files": children}, separators=(",", ":")).encode(), node


def archive(raw: bytes, code: bytes) -> bytes:
    return struct.pack("<4I", 4, 8 + len(raw), 0, len(raw)) + raw + code


class TestAsarReloadPatch(unittest.TestCase):
    def setUp(self) -> None:
        self.code = b"head:" + OLD_GATE + b";menu:" + OLD_KEY + b";tail"

    def test_marker_substitutions_preserve_length(self) -> None:
        self.assertEqual(len(OLD_GATE), len(NEW_GATE))
        self.assertEqual(len(OLD_KEY), len(NEW_KEY))
        modified, substitutions = patch["build_patch"](self.code)
        self.assertEqual(len(self.code), len(modified))
        self.assertEqual(len(substitutions), 2)
        self.assertEqual(patch["state"](self.code), "original")
        self.assertEqual(patch["state"](modified), "patched")

    def test_duplicate_original_markers_refused(self) -> None:
        with self.assertRaises(PatchError):
            patch["build_patch"](self.code + OLD_GATE)

    def test_second_patch_refused(self) -> None:
        modified, _ = patch["build_patch"](self.code)
        with self.assertRaises(PatchError):
            patch["build_patch"](modified)

    def test_integrity_metadata_and_file_offsets(self) -> None:
        raw, node = fixture(self.code)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "fixture.asar"
            path.write_bytes(archive(raw, self.code))
            read_raw, read_node, offset, read_code = patch["read_entry"](path)
            self.assertEqual((read_raw, read_code), (raw, self.code))
            self.assertEqual(offset, 16 + len(raw))
            self.assertEqual(read_node["integrity"]["hash"], digest(self.code))
            modified, _ = patch["build_patch"](self.code)
            changed_header = patch["patched_header"](raw, node, self.code, modified)
            self.assertEqual(len(changed_header), len(raw))
            path.write_bytes(archive(changed_header, modified))
            final_header, final_node, _, final_code = patch["read_entry"](path)
            self.assertEqual(final_header, changed_header)
            self.assertEqual(final_code, modified)
            self.assertEqual(final_node["integrity"]["hash"], digest(modified))
            block_size = final_node["integrity"]["blockSize"]
            self.assertEqual(final_node["integrity"]["blocks"], [
                digest(modified[i:i + block_size])
                for i in range(0, len(modified), block_size)
            ])

    def test_integrity_mismatch_refused(self) -> None:
        raw, node = fixture(self.code)
        modified, _ = patch["build_patch"](self.code)
        with self.assertRaises(PatchError):
            patch["patched_header"](raw, node, self.code + b"!", modified)

    def test_readonly_recon_finds_markers(self) -> None:
        raw, _ = fixture(self.code)
        with tempfile.TemporaryDirectory() as dirname:
            path = Path(dirname) / "fixture.asar"
            path.write_bytes(archive(raw, self.code))
            before = path.read_bytes()
            entries, size = recon["entry_list"](path)
            self.assertEqual(len(entries), 1)
            self.assertEqual(size, len(before))
            report = recon["scan"](path, max_excerpts=1)
            self.assertIn("allowwindowreload: 1", report)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
