#!/usr/bin/env python3
"""Read-only ChatGPT Electron ASAR index and targeted JavaScript diagnostics.

Default report omits user profiles, tokens, cookies and all unpacked binaries.
Does not modify the installation or write to its directory.
"""
from __future__ import annotations

import argparse
import json
import mmap
import os
import re
import struct
from collections import Counter
from pathlib import Path

DEFAULT_ASAR = Path("/usr/lib/chatgpt/resources/app.asar")
NEEDLES = (
    "allowWindowReload", "webContents.reload", "reloadIgnoringCache",
    "reloadWindow", "Reload Window", "window.location.reload",
    "thread/list", "thread/read", "thread/resume", "thread/loaded/list",
    "refreshRecentConversations", "refreshConversation",
    "invalidateQueries", "refetchQueries", "ipcMain.handle",
    "ipcRenderer.invoke", "CmdOrCtrl+R",
)


def entry_list(archive: Path) -> tuple[list[tuple[str, int, int]], int]:
    with archive.open("rb") as f:
        total = os.fstat(f.fileno()).st_size
        marker, header_size, _, json_size = struct.unpack("<4I", f.read(16))
        if marker != 4 or header_size < 8 or not (0 < json_size <= header_size - 8):
            raise ValueError("Invalid/unsupported ASAR header")
        if 8 + header_size >= total:
            raise ValueError("ASAR header exceeds file")
        header = json.loads(f.read(json_size))
    start = 8 + header_size
    entries = []

    def visit(children: dict, prefix: str = "") -> None:
        for name, entry in children.items():
            path = (prefix + "/" + name).lstrip("/")
            if "files" in entry:
                visit(entry["files"], path)
            elif not entry.get("unpacked") and "offset" in entry:
                offset, size = start + int(entry["offset"]), int(entry["size"])
                if 0 <= offset <= total and 0 <= size <= total - offset:
                    entries.append((path, offset, size))

    visit(header["files"])
    return entries, total


def scan(archive: Path, max_excerpts: int = 3) -> str:
    entries, total = entry_list(archive)
    scripts = [(p, pos, size) for p, pos, size in entries
               if p.endswith((".js", ".mjs", ".cjs"))]
    needles = [p.encode("ascii") for p in NEEDLES]
    regex = re.compile(b"|".join(map(re.escape, sorted(needles, key=len, reverse=True))),
                       re.IGNORECASE)
    counts = Counter()
    fragments = []
    shown = Counter()
    with archive.open("rb") as stream:
        with mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            for path, start, size in scripts:
                for match in regex.finditer(mm, start, start + size):
                    needle = match.group().decode("ascii").lower()
                    counts[needle] += 1
                    if shown[needle] >= max_excerpts:
                        continue
                    left = max(start, match.start() - 90)
                    right = min(start + size, match.end() + 120)
                    excerpt = mm[left:right].decode("utf-8", "replace")
                    excerpt = re.sub(r"\s+", " ", excerpt)
                    fragments.append((needle, path, excerpt))
                    shown[needle] += 1

    lines = [
        "# ChatGPT Linux — ASAR static research",
        "ASAR: " + str(archive),
        "Archive bytes: " + str(total),
        "Indexed entries: " + str(len(entries)),
        "JavaScript entries: " + str(len(scripts)),
        "",
        "## Counts",
    ]
    lines.extend(key + ": " + str(counts[key]) for key in sorted(counts))
    lines.append("")
    lines.append("## Bounded source excerpts")
    for needle, path, excerpt in fragments:
        lines.extend(("", "[" + needle + "] " + path, excerpt))
    lines.extend(("", "Only static code inspected; matches do not imply active functionality."))
    return "\n".join(lines) + "\n"


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--asar", type=Path, default=DEFAULT_ASAR)
    p.add_argument("--output", type=Path, help="Write report outside installation")
    p.add_argument("--excerpts", type=int, default=3)
    args = p.parse_args()
    if not (0 <= args.excerpts <= 10):
        p.error("--excerpts must be 0..10")
    report = scan(args.asar, args.excerpts)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
        print("Report written:", args.output)
    else:
        print(report, end="")


if __name__ == "__main__":
    main()
