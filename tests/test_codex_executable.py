"""Tests for shared, user-first Codex executable selection."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_usage_tray.codex_client import (
    CodexExecutableNotFoundError,
    build_codex_command,
)
from codex_usage_tray.codex_executable import (
    CodexExecutableResolutionError,
    resolve_codex_executable,
)
from codex_usage_tray.remote_control import (
    RemoteControlClient,
    RemoteControlPhase,
    RemoteControlUnavailableError,
)


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o700)


class _CompletedCommand:
    returncode = 0
    stdout = '{"status":"disabled"}'
    stderr = ""


class CodexExecutableResolverTests(unittest.TestCase):
    def test_user_local_codex_wins_over_path_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_home:
            user_codex = Path(temporary_home, ".local", "bin", "codex")
            _make_executable(user_codex)
            which = Mock(return_value="/system/path/codex")

            resolved = resolve_codex_executable(
                environ={"HOME": temporary_home, "PATH": "/system/path"},
                which=which,
            )

        self.assertEqual(resolved, str(user_codex))
        which.assert_not_called()

    def test_executable_environment_override_wins(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_home:
            override = Path(temporary_home, "selected-codex")
            user_codex = Path(temporary_home, ".local", "bin", "codex")
            _make_executable(override)
            _make_executable(user_codex)
            which = Mock(return_value="/system/path/codex")

            resolved = resolve_codex_executable(
                environ={
                    "HOME": temporary_home,
                    "PATH": "/system/path",
                    "CODEX_EXECUTABLE": str(override),
                },
                which=which,
            )

        self.assertEqual(resolved, str(override))
        which.assert_not_called()

    def test_invalid_overrides_are_rejected_without_exposing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_home:
            directory = Path(temporary_home, "codex-directory")
            directory.mkdir()
            non_executable = Path(temporary_home, "codex-not-executable")
            non_executable.write_text("test only", encoding="utf-8")
            user_codex = Path(temporary_home, ".local", "bin", "codex")
            _make_executable(user_codex)

            for invalid_override in (directory, non_executable):
                with self.subTest(override=invalid_override.name):
                    with self.assertRaises(
                        CodexExecutableResolutionError
                    ) as raised:
                        resolve_codex_executable(
                            environ={
                                "HOME": temporary_home,
                                "CODEX_EXECUTABLE": str(invalid_override),
                            },
                            which=Mock(return_value="/system/path/codex"),
                        )

                    self.assertEqual(
                        str(raised.exception), "Codex executable is unavailable"
                    )
                    self.assertNotIn(str(invalid_override), str(raised.exception))

    def test_falls_back_to_normal_which_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_home:
            which = Mock(return_value="/path-selected/codex")

            resolved = resolve_codex_executable(
                environ={"HOME": temporary_home, "PATH": "/path-selected"},
                which=which,
            )

        self.assertEqual(resolved, "/path-selected/codex")
        which.assert_called_once_with("codex")

    def test_missing_codex_keeps_both_client_errors_sanitized(self) -> None:
        private_detail = "/private/missing/codex"
        unavailable = CodexExecutableResolutionError(private_detail)

        with patch(
            "codex_usage_tray.codex_executable.resolve_codex_executable",
            side_effect=unavailable,
        ):
            with self.assertRaises(CodexExecutableNotFoundError) as usage_error:
                build_codex_command()

            runner = Mock(side_effect=AssertionError("runner must not be called"))
            with self.assertRaises(
                RemoteControlUnavailableError
            ) as remote_error:
                RemoteControlClient(command_runner=runner).start()

        self.assertEqual(
            str(usage_error.exception),
            "Codex executable was not found in PATH",
        )
        self.assertEqual(
            str(remote_error.exception),
            "Codex executable was not found in PATH",
        )
        self.assertNotIn(private_detail, str(usage_error.exception))
        self.assertNotIn(private_detail, str(remote_error.exception))
        runner.assert_not_called()

    def test_usage_and_remote_control_clients_share_the_resolver(self) -> None:
        runner = Mock(return_value=_CompletedCommand())
        with patch(
            "codex_usage_tray.codex_executable.resolve_codex_executable",
            return_value="/shared/resolved/codex",
        ) as resolver:
            usage_command = build_codex_command()
            remote_state = RemoteControlClient(command_runner=runner).start()

        self.assertEqual(usage_command[0], "/shared/resolved/codex")
        self.assertIs(remote_state.phase, RemoteControlPhase.STOPPED)
        self.assertEqual(resolver.call_count, 2)
        runner.assert_called_once_with(
            (
                "/shared/resolved/codex",
                "remote-control",
                "start",
                "--json",
            ),
            12.0,
        )


if __name__ == "__main__":
    unittest.main()
