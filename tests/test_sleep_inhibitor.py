"""Tests for inhibitor lifecycle without touching logind."""

import threading
import unittest
from unittest.mock import patch

from codex_usage_tray.sleep_inhibitor import InhibitorError, SleepInhibitor


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
    def run_thread(
        self, callback: object, errors: list[BaseException]
    ) -> threading.Thread:
        def target() -> None:
            try:
                callback()  # type: ignore[operator]
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=target)
        thread.start()
        return thread

    def join_thread(self, thread: threading.Thread) -> None:
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive(), "concurrency test thread did not finish")

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

    def test_concurrent_acquires_invoke_process_factory_once(self) -> None:
        process = FakeProcess()
        commands: list[tuple[str, ...]] = []
        factory_started = threading.Event()
        allow_factory = threading.Event()
        callers_ready = threading.Barrier(3)
        errors: list[BaseException] = []

        def factory(command: object) -> FakeProcess:
            commands.append(tuple(command))  # type: ignore[arg-type]
            factory_started.set()
            self.assertTrue(allow_factory.wait(timeout=2))
            return process

        def acquire() -> None:
            callers_ready.wait()
            inhibitor.set_required(True)

        inhibitor = SleepInhibitor(process_factory=factory)
        with patch(
            "codex_usage_tray.sleep_inhibitor.shutil.which",
            return_value="/test/systemd-inhibit",
        ):
            first = self.run_thread(acquire, errors)
            second = self.run_thread(acquire, errors)
            callers_ready.wait()
            self.assertTrue(factory_started.wait(timeout=2))
            allow_factory.set()
            self.join_thread(first)
            self.join_thread(second)

        self.assertEqual(errors, [])
        self.assertEqual(len(commands), 1)
        self.assertTrue(inhibitor.is_active)
        inhibitor.close()

    def test_release_waits_for_in_progress_acquire_and_terminates_it(self) -> None:
        process = FakeProcess()
        factory_started = threading.Event()
        allow_factory = threading.Event()
        release_started = threading.Event()
        errors: list[BaseException] = []

        def factory(_command: object) -> FakeProcess:
            factory_started.set()
            self.assertTrue(allow_factory.wait(timeout=2))
            return process

        def release() -> None:
            release_started.set()
            inhibitor.set_required(False)

        inhibitor = SleepInhibitor(process_factory=factory)
        with patch(
            "codex_usage_tray.sleep_inhibitor.shutil.which",
            return_value="/test/systemd-inhibit",
        ):
            acquire_thread = self.run_thread(
                lambda: inhibitor.set_required(True), errors
            )
            self.assertTrue(factory_started.wait(timeout=2))
            release_thread = self.run_thread(release, errors)
            self.assertTrue(release_started.wait(timeout=2))
            allow_factory.set()
            self.join_thread(acquire_thread)
            self.join_thread(release_thread)

        self.assertEqual(errors, [])
        self.assertFalse(inhibitor.is_active)
        self.assertEqual(process.terminate_calls, 1)

    def test_close_waits_for_in_progress_acquire_and_prevents_reacquire(self) -> None:
        process = FakeProcess()
        factory_started = threading.Event()
        allow_factory = threading.Event()
        close_started = threading.Event()
        factory_calls = 0
        errors: list[BaseException] = []

        def factory(_command: object) -> FakeProcess:
            nonlocal factory_calls
            factory_calls += 1
            factory_started.set()
            self.assertTrue(allow_factory.wait(timeout=2))
            return process

        def close() -> None:
            close_started.set()
            inhibitor.close()

        inhibitor = SleepInhibitor(process_factory=factory)
        with patch(
            "codex_usage_tray.sleep_inhibitor.shutil.which",
            return_value="/test/systemd-inhibit",
        ):
            acquire_thread = self.run_thread(inhibitor.acquire, errors)
            self.assertTrue(factory_started.wait(timeout=2))
            close_thread = self.run_thread(close, errors)
            self.assertTrue(close_started.wait(timeout=2))
            allow_factory.set()
            self.join_thread(acquire_thread)
            self.join_thread(close_thread)
            with self.assertRaises(InhibitorError):
                inhibitor.acquire()

        self.assertEqual(errors, [])
        self.assertFalse(inhibitor.is_active)
        self.assertEqual(process.terminate_calls, 1)
        self.assertEqual(factory_calls, 1)


if __name__ == "__main__":
    unittest.main()
