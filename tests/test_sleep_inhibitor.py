"""Tests for inhibitor lifecycle without touching logind."""

import unittest
from unittest.mock import patch

from codex_usage_tray.sleep_inhibitor import SleepInhibitor


class FakeProcess:
    def __init__(self) -> None:
        self.returncode: int | None = None
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminate_calls += 1
        self.returncode = 0

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        assert self.returncode is not None
        return self.returncode


class InhibitorLifecycleTests(unittest.TestCase):
    def test_acquires_only_once_and_uses_sleep_and_idle_scope(self) -> None:
        process = FakeProcess()
        commands: list[tuple[str, ...]] = []

        def factory(command: object) -> FakeProcess:
            commands.append(tuple(command))  # type: ignore[arg-type]
            return process

        inhibitor = SleepInhibitor(process_factory=factory)
        with patch(
            "codex_usage_tray.sleep_inhibitor.shutil.which",
            return_value="/test/systemd-inhibit",
        ):
            inhibitor.set_required(True)
            inhibitor.set_required(True)

        self.assertTrue(inhibitor.is_active)
        self.assertEqual(len(commands), 1)
        self.assertIn("--what=sleep:idle", commands[0])
        self.assertNotIn("handle-lid-switch", " ".join(commands[0]))

    def test_releases_when_no_longer_required(self) -> None:
        process = FakeProcess()
        inhibitor = SleepInhibitor(process_factory=lambda _command: process)
        with patch(
            "codex_usage_tray.sleep_inhibitor.shutil.which",
            return_value="/test/systemd-inhibit",
        ):
            inhibitor.set_required(True)
        inhibitor.set_required(False)

        self.assertFalse(inhibitor.is_active)
        self.assertEqual(process.terminate_calls, 1)

    def test_close_releases_active_inhibitor(self) -> None:
        process = FakeProcess()
        inhibitor = SleepInhibitor(process_factory=lambda _command: process)
        with patch(
            "codex_usage_tray.sleep_inhibitor.shutil.which",
            return_value="/test/systemd-inhibit",
        ):
            inhibitor.acquire()
        inhibitor.close()

        self.assertEqual(process.terminate_calls, 1)
        self.assertFalse(inhibitor.is_active)


if __name__ == "__main__":
    unittest.main()
