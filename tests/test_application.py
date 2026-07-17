"""Tests for tray coordination without GTK, desktop, network, or Codex."""

from __future__ import annotations

import threading
import unittest
from datetime import UTC

from codex_usage_tray.application import REFRESH_INTERVAL_SECONDS, TrayApplication
from codex_usage_tray.codex_client import CodexClientError
from codex_usage_tray.models import RateLimitSnapshot, RateLimitWindow


class FakeTray:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, tuple[str, ...], str]] = []

    def show_status(self, title: str, lines: tuple[str, ...], label: str) -> None:
        self.statuses.append((title, lines, label))


class FakeGtk:
    def __init__(self) -> None:
        self.tray = FakeTray()
        self.idle_callbacks: list[object] = []
        self.timeouts: list[tuple[int, object]] = []
        self.removed: list[int] = []
        self.quit_calls = 0
        self.refresh_callback = None
        self.quit_callback = None

    def idle_add(self, callback: object) -> int:
        self.idle_callbacks.append(callback)
        return len(self.idle_callbacks)

    def timeout_add(self, milliseconds: int, callback: object) -> int:
        self.timeouts.append((milliseconds, callback))
        return 99

    def source_remove(self, source_id: int) -> bool:
        self.removed.append(source_id)
        return True

    def main(self) -> None:
        return None

    def main_quit(self) -> None:
        self.quit_calls += 1

    def create_tray(self, on_refresh: object, on_quit: object) -> FakeTray:
        self.refresh_callback = on_refresh
        self.quit_callback = on_quit
        return self.tray

    def drain_idle(self) -> None:
        while self.idle_callbacks:
            callback = self.idle_callbacks.pop(0)
            callback()


class FakeClient:
    def __init__(self, snapshots: tuple[RateLimitSnapshot, ...] = ()) -> None:
        self.snapshots = snapshots
        self.start_calls = 0
        self.read_calls = 0
        self.close_calls = 0
        self.failure: Exception | None = None
        self.start_failure: Exception | None = None

    def start(self) -> None:
        self.start_calls += 1
        if self.start_failure:
            raise self.start_failure

    def read_rate_limits(self) -> tuple[RateLimitSnapshot, ...]:
        self.read_calls += 1
        if self.failure:
            raise self.failure
        return self.snapshots

    def close(self) -> None:
        self.close_calls += 1


def synchronous_worker(callback: object) -> None:
    callback()


class TrayApplicationTests(unittest.TestCase):
    def make_application(
        self, *clients: FakeClient, worker: object = synchronous_worker
    ) -> tuple[TrayApplication, FakeGtk]:
        gtk = FakeGtk()
        available_clients = iter(clients)
        return (
            TrayApplication(
                client_factory=lambda: next(available_clients),
                gtk=gtk,
                local_timezone=UTC,
                worker_launcher=worker,
            ),
            gtk,
        )

    def test_package_import_does_not_require_gtk(self) -> None:
        import codex_usage_tray

        self.assertIsNotNone(codex_usage_tray)

    def test_start_performs_immediate_refresh_and_uses_five_minute_period(self) -> None:
        client = FakeClient()
        app, gtk = self.make_application(client)

        app.start()

        self.assertEqual(gtk.timeouts[0][0], REFRESH_INTERVAL_SECONDS * 1000)
        self.assertEqual(gtk.timeouts[0][0], 300_000)
        self.assertEqual(client.start_calls, 1)
        self.assertEqual(client.read_calls, 1)

    def test_refresh_work_runs_off_the_gtk_thread(self) -> None:
        client = FakeClient()
        worker_threads: list[int] = []
        done = threading.Event()

        def worker(callback: object) -> None:
            def run() -> None:
                worker_threads.append(threading.get_ident())
                callback()
                done.set()

            threading.Thread(target=run).start()

        app, _ = self.make_application(client, worker=worker)
        main_thread = threading.get_ident()
        app.refresh()
        self.assertTrue(done.wait(1))
        self.assertNotEqual(worker_threads, [main_thread])

    def test_gtk_updates_are_dispatched_with_idle_add(self) -> None:
        app, gtk = self.make_application(FakeClient())
        app.refresh()
        self.assertEqual(gtk.tray.statuses, [])
        self.assertGreaterEqual(len(gtk.idle_callbacks), 2)
        gtk.drain_idle()
        self.assertEqual(gtk.tray.statuses[-1][1], ("No usage limits are available.",))

    def test_overlapping_refreshes_are_prevented(self) -> None:
        queued: list[object] = []
        app, _ = self.make_application(FakeClient(), worker=queued.append)
        app.refresh()
        app.refresh()
        self.assertEqual(len(queued), 1)

    def test_success_displays_all_windows_without_positional_semantics(self) -> None:
        snapshots = (
            RateLimitSnapshot(
                limit_id="codex",
                primary=RateLimitWindow(10, 10_080),
                secondary=RateLimitWindow(20, 300),
            ),
        )
        app, gtk = self.make_application(FakeClient(snapshots))
        app.refresh()
        gtk.drain_idle()
        title, lines, label = gtk.tray.statuses[-1]
        self.assertEqual(title, "codex")
        self.assertEqual(lines, ("7 days: 90% remaining", "5 hours: 80% remaining"))
        self.assertEqual(label, "80%")

    def test_empty_snapshot_and_sanitized_failure_have_safe_states(self) -> None:
        client = FakeClient()
        replacement = FakeClient()
        app, gtk = self.make_application(client, replacement)
        app.refresh()
        gtk.drain_idle()
        self.assertEqual(gtk.tray.statuses[-1][1], ("No usage limits are available.",))
        client.failure = CodexClientError("private account detail")
        app.refresh()
        gtk.drain_idle()
        self.assertEqual(gtk.tray.statuses[-1][1], ("Usage unavailable.",))

    def test_manual_refresh_and_quit_close_cleanly(self) -> None:
        client = FakeClient()
        app, gtk = self.make_application(client)
        app.start()
        initial_reads = client.read_calls
        gtk.refresh_callback()
        self.assertEqual(client.read_calls, initial_reads + 1)
        gtk.quit_callback()
        gtk.drain_idle()
        self.assertEqual(gtk.removed, [99])
        self.assertEqual(client.close_calls, 1)
        self.assertEqual(gtk.quit_calls, 1)

    def test_startup_failure_is_closed_and_recovers_with_a_new_client(self) -> None:
        failed = FakeClient()
        failed.start_failure = CodexClientError("sanitized startup failure")
        healthy = FakeClient()
        app, gtk = self.make_application(failed, healthy)

        app.refresh()
        app.refresh()
        gtk.drain_idle()

        self.assertEqual(failed.start_calls, 1)
        self.assertEqual(failed.close_calls, 1)
        self.assertEqual(failed.read_calls, 0)
        self.assertEqual(healthy.start_calls, 1)
        self.assertEqual(healthy.read_calls, 1)

    def test_read_failure_is_closed_and_recovers_with_a_new_client(self) -> None:
        failed = FakeClient()
        failed.failure = CodexClientError("sanitized connection failure")
        healthy = FakeClient()
        app, _ = self.make_application(failed, healthy)

        app.refresh()
        app.refresh()

        self.assertEqual(failed.start_calls, 1)
        self.assertEqual(failed.read_calls, 1)
        self.assertEqual(failed.close_calls, 1)
        self.assertEqual(healthy.start_calls, 1)
        self.assertEqual(healthy.read_calls, 1)

    def test_successive_healthy_refreshes_reuse_one_client(self) -> None:
        healthy = FakeClient()
        app, _ = self.make_application(healthy)

        app.refresh()
        app.refresh()

        self.assertEqual(healthy.start_calls, 1)
        self.assertEqual(healthy.read_calls, 2)


if __name__ == "__main__":
    unittest.main()
