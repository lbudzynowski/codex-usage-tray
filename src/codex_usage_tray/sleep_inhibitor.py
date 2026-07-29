"""Lifecycle management for a narrowly scoped logind sleep inhibitor.

The inhibitor does not change laptop-lid configuration. Whether logind honors
it on lid close remains controlled by the operating system, including its
``LidSwitchIgnoreInhibited`` setting.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable, Sequence
from threading import RLock
from typing import Protocol


class InhibitorError(RuntimeError):
    """A sanitized failure while acquiring the inhibitor."""


class InhibitorProcess(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def kill(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...


InhibitorProcessFactory = Callable[[Sequence[str]], InhibitorProcess]


def _spawn_inhibitor(command: Sequence[str]) -> InhibitorProcess:
    try:
        return subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        raise InhibitorError("Sleep inhibitor could not be started") from None


class SleepInhibitor:
    """Own one ``systemd-inhibit`` process and always release it explicitly."""

    def __init__(
        self,
        *,
        process_factory: InhibitorProcessFactory = _spawn_inhibitor,
        release_timeout: float = 1.0,
    ) -> None:
        self._process_factory = process_factory
        self._release_timeout = release_timeout
        self._process: InhibitorProcess | None = None
        self._closed = False
        self._lock = RLock()

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def set_required(self, required: bool) -> None:
        with self._lock:
            if required:
                self.acquire()
            else:
                self.release()

    def acquire(self) -> None:
        with self._lock:
            if self._closed:
                raise InhibitorError("Sleep inhibitor is closed")
            if self.is_active:
                return
            self._process = None
            executable = shutil.which("systemd-inhibit")
            if executable is None:
                raise InhibitorError("systemd-inhibit is unavailable")
            command = (
                executable,
                "--what=sleep:idle",
                "--who=Codex Usage Tray",
                "--why=Codex Remote Control is active",
                "--mode=block",
                "sleep",
                "infinity",
            )
            try:
                process = self._process_factory(command)
            except InhibitorError:
                raise
            except Exception:
                raise InhibitorError("Sleep inhibitor could not be started") from None
            if process.poll() is not None:
                raise InhibitorError("Sleep inhibitor exited during startup")
            self._process = process

    def release(self) -> None:
        with self._lock:
            process = self._process
            if process is None:
                return
            if process.poll() is not None:
                self._process = None
                return
            process.terminate()
            try:
                process.wait(timeout=self._release_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=self._release_timeout)
                except subprocess.TimeoutExpired:
                    # Retain ownership so a later release can try again.
                    raise InhibitorError("Sleep inhibitor did not stop") from None
            self._process = None

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self.release()
