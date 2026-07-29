"""Tests for repository-maintained package launcher behavior."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


class PackagedLauncherTests(unittest.TestCase):
    def test_launcher_path_prefers_user_local_codex(self) -> None:
        repository = Path(__file__).resolve().parents[1]
        launcher = repository / "packaging" / "debian" / "codex-usage-tray"
        path_assignment = next(
            line for line in launcher.read_text(encoding="utf-8").splitlines()
            if line.startswith("PATH=")
        )

        with tempfile.TemporaryDirectory() as temporary_root:
            home = Path(temporary_root, "home")
            path_bin = Path(temporary_root, "path-bin")
            user_codex = home / ".local" / "bin" / "codex"
            path_codex = path_bin / "codex"
            _make_executable(user_codex)
            _make_executable(path_codex)

            completed = subprocess.run(
                ("/bin/sh", "-c", f"{path_assignment}\ncommand -v codex"),
                env={"HOME": str(home), "PATH": str(path_bin)},
                check=True,
                capture_output=True,
                text=True,
                timeout=2.0,
            )

        self.assertEqual(completed.stdout.strip(), str(user_codex))


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
