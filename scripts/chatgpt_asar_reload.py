#!/usr/bin/env python3
"""Reversible, version-pinned ChatGPT Linux ASAR Reload Window patch.

This is an optional developer tool, not part of the tray's runtime or .deb.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

VERSION = "26.1027.51404"
EXPECTED_PREFIX = "9d9a862d8c6eb31b9092ecd50fa1cd82b729621720deea1b2f342f827556fc82"
APP = Path("/usr/lib/chatgpt/resources/app.asar")
BACKUP = Path("/var/backups/chatgpt-asar") / ("app.asar." + VERSION + ".original")
ENTRY = ".vite/build/early-bootstrap-439-a0f25ba32c465de1.js"

OLD_GATE = b"allowWindowReload:r!==eH.x.Prod"
NEW_GATE = b"allowWindowReload:!0" + b" " * (len(OLD_GATE) - len(b"allowWindowReload:!0"))
OLD_KEY = b'accelerator:"Command+Control+R"'
NEW_KEY = b'accelerator:"Control+Shift+R"'
NEW_KEY += b" " * (len(OLD_KEY) - len(NEW_KEY))


class PatchError(Exception):
    pass


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def prefix_hash(path: Path) -> str:
    with path.open("rb") as f:
        return digest(f.read(1024 * 1024))


def assert_package() -> None:
    result = subprocess.run(
        ["dpkg-query", "-W", "-f=$" + "{Version}", "chatgpt"],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0 or result.stdout.strip() != VERSION:
        raise PatchError("Expected installed chatgpt " + VERSION + "; no changes made.")
    if not APP.is_file() or APP.is_symlink():
        raise PatchError("ASAR not found, or it is a symlink.")


def assert_stopped() -> None:
    result = subprocess.run(["pgrep", "-x", "ChatGPT"], capture_output=True, check=False)
    if result.returncode != 1:
        raise PatchError("ChatGPT may still be running; stop it before modifying ASAR.")


def node_for(header: dict) -> dict:
    children = header["files"]
    node = {}
    for part in ENTRY.split("/"):
        node = children[part]
        if "files" in node:
            children = node["files"]
    if "offset" not in node or "files" in node or node.get("unpacked"):
        raise PatchError("Unexpected ASAR file layout.")
    return node


def read_entry(path: Path) -> tuple[bytes, dict, int, bytes]:
    total = path.stat().st_size
    with path.open("rb") as f:
        first = f.read(16)
        if len(first) != 16:
            raise PatchError("Invalid ASAR.")
        marker, header_size, _, json_size = struct.unpack("<4I", first)
        if marker != 4 or header_size < 8 or not (0 < json_size <= header_size - 8):
            raise PatchError("Unsupported ASAR index.")
        if 8 + header_size >= total:
            raise PatchError("ASAR header exceeds file.")
        raw = f.read(json_size)
        node = node_for(json.loads(raw))
        start = 8 + header_size + int(node["offset"])
        size = int(node["size"])
        if not (0 <= start < total and 0 < size <= total - start):
            raise PatchError("Invalid ASAR entry bounds.")
        f.seek(start)
        code = f.read(size)
    return raw, node, start, code


def state(code: bytes) -> str:
    if code.count(OLD_GATE) == 1 and code.count(OLD_KEY) == 1:
        return "original"
    if code.count(NEW_GATE) == 1 and code.count(NEW_KEY) == 1:
        return "patched"
    return "unknown"


def build_patch(code: bytes) -> tuple[bytes, list[tuple[int, bytes]]]:
    if state(code) != "original":
        raise PatchError("Exact original markers missing or already patched.")
    modified = bytearray(code)
    offsets = []
    for old, new in ((OLD_GATE, NEW_GATE), (OLD_KEY, NEW_KEY)):
        if len(old) != len(new) or code.count(old) != 1:
            raise PatchError("Unsafe or ambiguous substitution.")
        pos = code.find(old)
        offsets.append((pos, new))
        modified[pos:pos + len(old)] = new
    return bytes(modified), offsets


def patched_header(raw: bytes, node: dict, original: bytes, modified: bytes) -> bytes:
    meta = node.get("integrity")
    if not meta:
        return raw
    if meta.get("algorithm") != "SHA256":
        raise PatchError("Unsupported integrity algorithm.")
    block_size = meta.get("blockSize")
    if not isinstance(block_size, int) or block_size <= 0:
        raise PatchError("Invalid integrity block size.")
    original_blocks = [digest(original[i:i + block_size])
                       for i in range(0, len(original), block_size)]
    modified_blocks = [digest(modified[i:i + block_size])
                       for i in range(0, len(modified), block_size)]
    if meta["hash"] != digest(original) or meta["blocks"] != original_blocks:
        raise PatchError("Original ASAR integrity mismatch.")
    out = bytearray(raw)
    for old, new in [(meta["hash"], digest(modified)), *zip(original_blocks, modified_blocks)]:
        if old == new:
            continue
        before = old.encode("ascii")
        if out.count(before) != 1:
            raise PatchError("Ambiguous integrity metadata replacement.")
        pos = out.find(before)
        out[pos:pos + len(before)] = new.encode("ascii")
    updated = node_for(json.loads(out))
    if updated["integrity"]["hash"] != digest(modified):
        raise PatchError("Updated integrity verification failed.")
    return bytes(out)


def apply() -> None:
    assert_stopped()
    if prefix_hash(APP) != EXPECTED_PREFIX:
        raise PatchError("This ASAR does not match the verified unmodified build.")
    raw, node, start, code = read_entry(APP)
    modified, patches = build_patch(code)
    new_header = patched_header(raw, node, code, modified)
    if BACKUP.exists():
        raise PatchError("Backup already exists; refusing to replace it.")
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(BACKUP.parent).free < APP.stat().st_size + 128 * 1024 * 1024:
        raise PatchError("Insufficient space for verified backup.")
    temp = BACKUP.with_name(BACKUP.name + ".partial")
    if temp.exists():
        raise PatchError("Unresolved partial backup exists.")
    try:
        shutil.copy2(APP, temp)
        if file_hash(APP) != file_hash(temp):
            raise PatchError("Backup SHA256 verification failed.")
        os.replace(temp, BACKUP)
    finally:
        if temp.exists():
            temp.unlink()
    try:
        with APP.open("r+b", buffering=0) as f:
            for relative_offset, replacement in patches:
                f.seek(start + relative_offset)
                f.write(replacement)
            if raw != new_header:
                f.seek(16)
                f.write(new_header)
            os.fsync(f.fileno())
        actual_header, actual_node, _, actual_code = read_entry(APP)
        if actual_header != new_header or actual_code != modified or state(actual_code) != "patched":
            raise PatchError("Patched ASAR read-back failed.")
        meta = actual_node.get("integrity")
        if meta and meta["hash"] != digest(actual_code):
            raise PatchError("Patched file integrity failed.")
    except BaseException:
        shutil.copy2(BACKUP, APP)
        raise
    print("PATCH OK - Reload Window enabled; Ctrl+Shift+R.")
    print("Backup:", BACKUP)


def restore() -> None:
    assert_stopped()
    if not BACKUP.is_file() or BACKUP.is_symlink():
        raise PatchError("Original backup missing.")
    if prefix_hash(BACKUP) != EXPECTED_PREFIX:
        raise PatchError("Backup fingerprint mismatch.")
    _, _, _, backup_code = read_entry(BACKUP)
    _, _, _, current_code = read_entry(APP)
    if state(backup_code) != "original" or state(current_code) != "patched":
        raise PatchError("Unexpected backup/current patch state; refusing overwrite.")
    if APP.stat().st_size != BACKUP.stat().st_size:
        raise PatchError("ASAR size mismatch; refusing overwrite.")
    shutil.copy2(BACKUP, APP)
    if file_hash(APP) != file_hash(BACKUP):
        raise PatchError("Restore failed verification; original backup remains intact.")
    print("RESTORE OK")


def status() -> None:
    _, node, _, code = read_entry(APP)
    print("ChatGPT version:", VERSION)
    print("ASAR:", APP)
    print("Entry:", ENTRY)
    print("Patch state:", state(code))
    print("Integrity metadata:", "present" if node.get("integrity") else "absent")
    print("Original fingerprint:", prefix_hash(APP) == EXPECTED_PREFIX)
    print("Backup found:", BACKUP.exists())


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument("--status", action="store_true", help="Read-only patch status")
    modes.add_argument("--apply", action="store_true", help="Back up and patch")
    modes.add_argument("--restore", action="store_true", help="Restore verified original")
    args = p.parse_args()
    try:
        assert_package()
        if args.status:
            status()
        elif args.apply:
            apply()
        else:
            restore()
        return 0
    except (PatchError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print("STOP:", exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
