"""Tests for Remote Control CLI parsing with no real lifecycle commands."""

from __future__ import annotations

import subprocess
import unittest
from dataclasses import dataclass
from unittest.mock import patch

from codex_usage_tray.pairing import PairingArtifact
from codex_usage_tray.remote_control import (
    RemoteControlClient,
    RemoteControlCommandError,
    RemoteControlPhase,
    RemoteControlProtocolError,
    RemoteControlTimeoutError,
    RemoteControlUnavailableError,
    _run_command,
    parse_remote_control_status,
)

TEST_MANUAL_CODE = "TEST-CODE-ONLY"
TEST_PAIRING_PAYLOAD = "test-pairing-payload-not-valid"


@dataclass
class FakeCompleted:
    returncode: int = 0
    stdout: str = "{}"
    stderr: str = ""


class CapturingRunner:
    def __init__(self, *results: FakeCompleted) -> None:
        self.results = list(results)
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command: object, _timeout: float) -> FakeCompleted:
        self.commands.append(tuple(command))  # type: ignore[arg-type]
        return self.results.pop(0)


class StatusParsingTests(unittest.TestCase):
    def test_parses_valid_start_json_and_hostname(self) -> None:
        runner = CapturingRunner(
            FakeCompleted(
                stdout='{"mode":"daemon","status":"connected","serverName":"Bhola",'
                '"environmentId":"test-environment","timedOut":false}'
            )
        )
        client = RemoteControlClient(
            command_runner=runner, executable="fake-codex", timeout=0.1
        )

        state = client.start()

        self.assertIs(state.phase, RemoteControlPhase.CONNECTED)
        self.assertEqual(state.server_name, "Bhola")
        self.assertEqual(
            runner.commands,
            [("fake-codex", "remote-control", "start", "--json")],
        )

    def test_tolerates_missing_optional_status_fields(self) -> None:
        state = parse_remote_control_status({"status": "connecting"})
        self.assertIs(state.phase, RemoteControlPhase.DISCONNECTED)
        self.assertIsNone(state.server_name)

    def test_maps_disabled_and_errored_statuses(self) -> None:
        self.assertIs(
            parse_remote_control_status({"status": "disabled"}).phase,
            RemoteControlPhase.STOPPED,
        )
        self.assertIs(
            parse_remote_control_status({"status": "errored"}).phase,
            RemoteControlPhase.ERROR,
        )

    def test_rejects_invalid_json(self) -> None:
        client = RemoteControlClient(
            command_runner=CapturingRunner(FakeCompleted(stdout="not-json")),
            executable="fake-codex",
        )
        with self.assertRaises(RemoteControlProtocolError):
            client.start()

    def test_nonzero_subprocess_error_is_sanitized(self) -> None:
        client = RemoteControlClient(
            command_runner=CapturingRunner(
                FakeCompleted(returncode=7, stderr="backend failed")
            ),
            executable="fake-codex",
        )
        with self.assertRaises(RemoteControlCommandError) as raised:
            client.start()
        self.assertNotIn("backend failed", str(raised.exception))

    def test_missing_pair_subcommand_is_unavailable(self) -> None:
        runner = CapturingRunner(
            FakeCompleted(
                returncode=2,
                stderr="error: unrecognized subcommand 'pair'",
            )
        )
        client = RemoteControlClient(
            command_runner=runner, executable="fake-codex"
        )
        with self.assertRaises(RemoteControlUnavailableError):
            client.pair()
        self.assertEqual(
            runner.commands,
            [("fake-codex", "remote-control", "pair", "--json")],
        )

    def test_pairing_stdout_and_stderr_never_enter_errors(self) -> None:
        client = RemoteControlClient(
            command_runner=CapturingRunner(
                FakeCompleted(
                    returncode=1,
                    stdout=TEST_MANUAL_CODE,
                    stderr=f"failed near {TEST_PAIRING_PAYLOAD}",
                )
            ),
            executable="fake-codex",
        )

        with self.assertRaises(RemoteControlCommandError) as raised:
            client.pair()

        self.assertNotIn(TEST_MANUAL_CODE, str(raised.exception))
        self.assertNotIn(TEST_PAIRING_PAYLOAD, str(raised.exception))

    def test_parses_fictional_pairing_response_only_in_memory(self) -> None:
        client = RemoteControlClient(
            command_runner=CapturingRunner(
                FakeCompleted(
                    stdout=(
                        '{"pairingCode":"test-pairing-payload-not-valid",'
                        '"manualPairingCode":"TEST-CODE-ONLY","expiresAt":200}'
                    )
                )
            ),
            executable="fake-codex",
        )

        artifact = client.pair()

        self.assertEqual(
            artifact,
            PairingArtifact(TEST_MANUAL_CODE, TEST_PAIRING_PAYLOAD, 200),
        )

    def test_stop_parses_stopped_and_never_runs_a_real_command(self) -> None:
        runner = CapturingRunner(FakeCompleted(stdout='{"status":"notRunning"}'))
        client = RemoteControlClient(
            command_runner=runner, executable="fake-codex"
        )
        self.assertIs(client.stop().phase, RemoteControlPhase.STOPPED)
        self.assertEqual(
            runner.commands,
            [("fake-codex", "remote-control", "stop", "--json")],
        )

    def test_passive_status_reports_stopped_when_daemon_is_not_running(self) -> None:
        runner = CapturingRunner(
            FakeCompleted(returncode=1, stderr="daemon is not running")
        )

        def unexpected_proxy(_command: object) -> object:
            self.fail("a stopped daemon must not open a proxy")

        client = RemoteControlClient(
            command_runner=runner,
            proxy_factory=unexpected_proxy,  # type: ignore[arg-type]
            executable="fake-codex",
        )

        self.assertIs(client.read_status().phase, RemoteControlPhase.STOPPED)
        self.assertEqual(
            runner.commands,
            [("fake-codex", "app-server", "daemon", "version")],
        )

    def test_passive_status_does_not_hide_other_daemon_errors(self) -> None:
        runner = CapturingRunner(
            FakeCompleted(returncode=1, stderr="permission denied: private detail")
        )
        client = RemoteControlClient(
            command_runner=runner,
            executable="fake-codex",
        )

        with self.assertRaises(RemoteControlCommandError) as raised:
            client.read_status()

        self.assertNotIn("private detail", str(raised.exception))
        self.assertEqual(
            runner.commands,
            [("fake-codex", "app-server", "daemon", "version")],
        )


class SubprocessTimeoutTests(unittest.TestCase):
    def test_timeout_is_converted_to_a_sanitized_error(self) -> None:
        with patch(
            "codex_usage_tray.remote_control.subprocess.run",
            side_effect=subprocess.TimeoutExpired(("fake-codex",), 0.01),
        ):
            with self.assertRaises(RemoteControlTimeoutError):
                _run_command(("fake-codex",), 0.01)


if __name__ == "__main__":
    unittest.main()
