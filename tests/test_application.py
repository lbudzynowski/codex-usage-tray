"""Tests for tray coordination without GTK, desktop, network, or Codex."""

from __future__ import annotations

import threading
import unittest
from datetime import UTC

from codex_usage_tray.application import REFRESH_INTERVAL_SECONDS, TrayApplication
from codex_usage_tray.codex_client import CodexAccount, CodexAccountState, CodexClientError, CodexLoginCompletion, CodexLoginStart
from codex_usage_tray.models import RateLimitSnapshot, RateLimitWindow
from codex_usage_tray.pairing import PairingArtifact
from codex_usage_tray.remote_control import (
    RemoteControlPhase,
    RemoteControlState,
    RemoteControlUnavailableError,
)


class FakeTray:
    def __init__(self) -> None:
        self.statuses: list[tuple[str, tuple[str, ...], str]] = []
        self.remote_statuses: list[tuple[object, ...]] = []
        self.remote_callbacks: tuple[object, ...] | None = None

    def show_status(self, title: str, lines: tuple[str, ...], label: str, *, sign_in_available: bool = False, account_info: str | None = None) -> None:
        self.statuses.append((title, lines, label, sign_in_available, account_info))

    def configure_remote_control(self, *callbacks: object) -> None:
        self.remote_callbacks = callbacks

    def show_remote_control(self, status: str, **values: object) -> None:
        self.remote_statuses.append((status, values))


class FakePairingView:
    def __init__(self) -> None:
        self.remaining: list[int | None] = []
        self.copied = 0
        self.claimed = 0
        self.expired = 0
        self.errors = 0
        self.closed = 0

    def show_remaining(self, seconds: int | None) -> None:
        self.remaining.append(seconds)

    def show_copied(self) -> None:
        self.copied += 1

    def show_claimed(self) -> None:
        self.claimed += 1

    def show_expired(self) -> None:
        self.expired += 1

    def show_error(self) -> None:
        self.errors += 1

    def close(self) -> None:
        self.closed += 1


class FakeGtk:
    def __init__(self) -> None:
        self.tray = FakeTray()
        self.idle_callbacks: list[object] = []
        self.timeouts: list[tuple[int, object]] = []
        self.removed: list[int] = []
        self.quit_calls = 0
        self.refresh_callback = None
        self.quit_callback = None
        self.sign_in_callback = None
        self.dialog_responses: list[bool] = []
        self.dialog_calls = 0
        self.pairing_views: list[FakePairingView] = []
        self.pairing_dialogs: list[tuple[str, object, object, object]] = []
        self.pairing_errors: list[str] = []
        self.clipboard: list[str] = []

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

    def create_tray(self, on_refresh: object, on_quit: object, on_sign_in: object) -> FakeTray:
        self.refresh_callback = on_refresh
        self.quit_callback = on_quit
        self.sign_in_callback = on_sign_in
        return self.tray

    def show_sign_in_dialog(self) -> bool:
        self.dialog_calls += 1
        return self.dialog_responses.pop(0) if self.dialog_responses else False

    def show_pairing_dialog(self, manual_code: str, *, on_copy: object, on_new: object, on_closed: object) -> FakePairingView:
        view = FakePairingView()
        self.pairing_views.append(view)
        self.pairing_dialogs.append((manual_code, on_copy, on_new, on_closed))
        return view

    def show_pairing_error(self, message: str) -> None:
        self.pairing_errors.append(message)

    def copy_to_clipboard(self, text: str) -> None:
        self.clipboard.append(text)

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
        self.account_calls = 0
        self.login_calls = 0
        self.completions: list[CodexLoginCompletion] = []
        self.account_state = CodexAccountState(account=CodexAccount("chatgpt", "user@example.test", "plus"), requires_openai_auth=True)
        self.failure: Exception | None = None
        self.start_failure: Exception | None = None

    def start(self) -> None:
        self.start_calls += 1
        if self.start_failure:
            raise self.start_failure

    def read_account(self) -> CodexAccountState:
        self.account_calls += 1
        if self.failure:
            raise self.failure
        return self.account_state

    def read_rate_limits(self) -> tuple[RateLimitSnapshot, ...]:
        self.read_calls += 1
        if getattr(self, "read_failure", None):
            raise self.read_failure
        if self.failure:
            raise self.failure
        return self.snapshots

    def start_chatgpt_login(self) -> CodexLoginStart:
        self.login_calls += 1
        return CodexLoginStart("login-1", "https://chatgpt.com/auth")

    def pop_login_completion(self) -> CodexLoginCompletion | None:
        return self.completions.pop(0) if self.completions else None

    def close(self) -> None:
        self.close_calls += 1
        if getattr(self, "close_failure", None):
            raise self.close_failure


class FakeRemoteControl:
    def __init__(self) -> None:
        self.state = RemoteControlState(RemoteControlPhase.STOPPED)
        self.start_state = RemoteControlState(RemoteControlPhase.CONNECTED, "Bhola")
        self.stop_state = RemoteControlState(RemoteControlPhase.STOPPED)
        self.artifact = PairingArtifact(
            "TEST-CODE-ONLY", "test-pairing-payload-not-valid", 9_999_999_999
        )
        self.claimed = False
        self.calls: list[str] = []
        self.status_failure: Exception | None = None

    def read_status(self) -> RemoteControlState:
        self.calls.append("status")
        if self.status_failure is not None:
            raise self.status_failure
        return self.state

    def start(self) -> RemoteControlState:
        self.calls.append("start")
        return self.start_state

    def stop(self) -> RemoteControlState:
        self.calls.append("stop")
        return self.stop_state

    def pair(self) -> PairingArtifact:
        self.calls.append("pair")
        return self.artifact

    def pairing_claimed(self, _artifact: PairingArtifact) -> bool:
        self.calls.append("pair-status")
        return self.claimed


class FakeInhibitor:
    def __init__(self) -> None:
        self.is_active = False
        self.required: list[bool] = []
        self.close_calls = 0

    def set_required(self, required: bool) -> None:
        self.required.append(required)
        self.is_active = required

    def close(self) -> None:
        self.close_calls += 1
        self.is_active = False


def synchronous_worker(callback: object) -> None:
    callback()


class TrayApplicationTests(unittest.TestCase):
    def make_application(
        self,
        *clients: FakeClient,
        worker: object = synchronous_worker,
        remote: FakeRemoteControl | None = None,
        inhibitor: FakeInhibitor | None = None,
    ) -> tuple[TrayApplication, FakeGtk]:
        gtk = FakeGtk()
        available_clients = iter(clients)
        return (
            TrayApplication(
                client_factory=lambda: next(available_clients),
                gtk=gtk,
                local_timezone=UTC,
                worker_launcher=worker,
                remote_control=remote,
                sleep_inhibitor=inhibitor,
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
        title, lines, label, _sign_in, account_info = gtk.tray.statuses[-1]
        self.assertEqual(title, "codex")
        self.assertEqual(account_info, "Account: user@example.test — Plus")
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
        self.assertEqual(gtk.removed, [99, 99])
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
        failed.read_failure = CodexClientError("sanitized connection failure")
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

    def test_account_check_occurs_before_rate_limit_read(self) -> None:
        client = FakeClient()
        app, _ = self.make_application(client)
        app.refresh()
        self.assertEqual(client.account_calls, 1)
        self.assertEqual(client.read_calls, 1)

    def test_signed_out_state_and_prompt_once_not_now_keeps_running(self) -> None:
        client = FakeClient()
        client.account_state = CodexAccountState(account=None, requires_openai_auth=True)
        app, gtk = self.make_application(client)
        app.refresh(); gtk.drain_idle()
        app.refresh(); gtk.drain_idle()
        self.assertEqual(gtk.tray.statuses[-1][1], ("Codex is not signed in.",))
        self.assertEqual(gtk.tray.statuses[-1][3], True)
        self.assertEqual(gtk.dialog_calls, 1)
        self.assertEqual(gtk.quit_calls, 0)
        self.assertEqual(client.read_calls, 0)

    def test_sign_in_uses_injected_opener_and_prevents_duplicates(self) -> None:
        client = FakeClient()
        opened: list[str] = []
        queued: list[object] = []
        app, _ = self.make_application(client, worker=queued.append)
        app._url_opener = lambda url: opened.append(url) or True
        app.sign_in(); app.sign_in()
        self.assertEqual(len(queued), 1)
        queued.pop(0)()
        self.assertEqual(opened, ["https://chatgpt.com/auth"])
        self.assertEqual(client.login_calls, 1)

    def test_successful_login_completion_refreshes_immediately(self) -> None:
        client = FakeClient()
        app, gtk = self.make_application(client)
        app._url_opener = lambda _url: True
        app.sign_in(); gtk.drain_idle()
        self.assertEqual(client.login_calls, 1)
        client.completions.append(CodexLoginCompletion("login-1", True, False))
        app._on_login_poll(); gtk.drain_idle()
        self.assertGreaterEqual(client.account_calls, 1)
        self.assertGreaterEqual(client.read_calls, 1)

    def test_failed_login_and_browser_failure_are_sanitized(self) -> None:
        client = FakeClient()
        app, gtk = self.make_application(client)
        app._url_opener = lambda _url: False
        app.sign_in(); gtk.drain_idle()
        self.assertEqual(gtk.tray.statuses[-1][1], ("Could not open the browser. Run `codex login` in a terminal.",))
        self.assertNotIn("chatgpt.com", gtk.tray.statuses[-1][1][0])

        client.completions.append(CodexLoginCompletion("login-1", False, True))
        app._pending_login_id = "login-1"
        app._login_pending = True
        app._on_login_poll(); gtk.drain_idle()
        self.assertEqual(gtk.tray.statuses[-1][1], ("Codex sign-in failed.",))

    def test_remote_control_transitions_and_action_availability(self) -> None:
        remote = FakeRemoteControl()
        app, gtk = self.make_application(FakeClient(), remote=remote)

        app.start()
        gtk.drain_idle()
        self.assertEqual(remote.calls, ["status"])
        status, values = gtk.tray.remote_statuses[-1]
        self.assertEqual(status, "Remote Control: Stopped")
        self.assertTrue(values["start_enabled"])
        self.assertFalse(values["pair_enabled"])
        self.assertTrue(values["prevent_sleep"])

        app.start_remote_control()
        gtk.drain_idle()
        status, values = gtk.tray.remote_statuses[-1]
        self.assertEqual(status, "Remote Control: Connected — Bhola")
        self.assertFalse(values["start_enabled"])
        self.assertTrue(values["stop_enabled"])
        self.assertTrue(values["pair_enabled"])

        app.stop_remote_control()
        gtk.drain_idle()
        self.assertEqual(gtk.tray.remote_statuses[-1][0], "Remote Control: Stopped")
        self.assertEqual(remote.calls, ["status", "start", "stop"])

    def test_remote_control_unavailable_disables_all_actions(self) -> None:
        remote = FakeRemoteControl()
        remote.status_failure = RemoteControlUnavailableError("unsupported")
        app, gtk = self.make_application(FakeClient(), remote=remote)

        app.start()
        gtk.drain_idle()

        status, values = gtk.tray.remote_statuses[-1]
        self.assertEqual(status, "Remote Control: Unavailable")
        self.assertFalse(values["start_enabled"])
        self.assertFalse(values["stop_enabled"])
        self.assertFalse(values["pair_enabled"])

    def test_connected_remote_control_acquires_inhibitor_by_default(self) -> None:
        remote = FakeRemoteControl()
        remote.state = RemoteControlState(RemoteControlPhase.CONNECTED, "Bhola")
        inhibitor = FakeInhibitor()
        app, gtk = self.make_application(
            FakeClient(), remote=remote, inhibitor=inhibitor
        )

        app.start()
        gtk.drain_idle()

        self.assertTrue(inhibitor.is_active)
        self.assertTrue(inhibitor.required[-1])
        self.assertTrue(gtk.tray.remote_statuses[-1][1]["prevent_sleep"])

    def test_user_can_disable_default_sleep_protection_for_current_session(self) -> None:
        remote = FakeRemoteControl()
        remote.state = RemoteControlState(RemoteControlPhase.CONNECTED, "Bhola")
        inhibitor = FakeInhibitor()
        app, gtk = self.make_application(
            FakeClient(), remote=remote, inhibitor=inhibitor
        )
        app.start()

        app.set_prevent_sleep(False)
        gtk.drain_idle()

        self.assertFalse(inhibitor.is_active)
        self.assertFalse(inhibitor.required[-1])
        self.assertFalse(gtk.tray.remote_statuses[-1][1]["prevent_sleep"])

    def test_non_connected_states_do_not_acquire_inhibitor(self) -> None:
        for phase in (
            RemoteControlPhase.STOPPED,
            RemoteControlPhase.DISCONNECTED,
            RemoteControlPhase.ERROR,
            RemoteControlPhase.UNAVAILABLE,
        ):
            with self.subTest(phase=phase):
                remote = FakeRemoteControl()
                remote.state = RemoteControlState(phase, "Bhola")
                inhibitor = FakeInhibitor()
                app, gtk = self.make_application(
                    FakeClient(), remote=remote, inhibitor=inhibitor
                )

                app.start()
                gtk.drain_idle()

                self.assertFalse(inhibitor.is_active)
                self.assertFalse(inhibitor.required[-1])

    def test_leaving_connected_state_releases_inhibitor(self) -> None:
        for phase in (
            RemoteControlPhase.STOPPED,
            RemoteControlPhase.DISCONNECTED,
            RemoteControlPhase.ERROR,
            RemoteControlPhase.UNAVAILABLE,
        ):
            with self.subTest(phase=phase):
                remote = FakeRemoteControl()
                remote.state = RemoteControlState(
                    RemoteControlPhase.CONNECTED, "Bhola"
                )
                inhibitor = FakeInhibitor()
                app, gtk = self.make_application(
                    FakeClient(), remote=remote, inhibitor=inhibitor
                )
                app.start()
                gtk.drain_idle()
                self.assertTrue(inhibitor.is_active)

                remote.state = RemoteControlState(phase, "Bhola")
                app.refresh_remote_control()
                gtk.drain_idle()

                self.assertFalse(inhibitor.is_active)
                self.assertFalse(inhibitor.required[-1])

    def test_quit_releases_inhibitor_without_stopping_remote_control(self) -> None:
        remote = FakeRemoteControl()
        remote.state = RemoteControlState(RemoteControlPhase.CONNECTED, "Bhola")
        inhibitor = FakeInhibitor()
        app, gtk = self.make_application(
            FakeClient(), remote=remote, inhibitor=inhibitor
        )
        app.start()
        gtk.drain_idle()
        self.assertTrue(inhibitor.is_active)
        remote.calls.clear()

        app.quit()
        gtk.drain_idle()

        self.assertEqual(inhibitor.close_calls, 1)
        self.assertFalse(inhibitor.is_active)
        self.assertNotIn("stop", remote.calls)

    def test_quit_releases_inhibitor_even_if_usage_client_close_fails(self) -> None:
        client = FakeClient()
        client.close_failure = CodexClientError("sanitized close failure")
        inhibitor = FakeInhibitor()
        app, gtk = self.make_application(client, inhibitor=inhibitor)
        app.start()

        app.quit()
        gtk.drain_idle()

        self.assertEqual(inhibitor.close_calls, 1)
        self.assertEqual(gtk.quit_calls, 1)

    def test_pairing_copy_claim_and_expiry_use_only_fictional_values(self) -> None:
        remote = FakeRemoteControl()
        remote.state = RemoteControlState(RemoteControlPhase.CONNECTED, "Bhola")
        app, gtk = self.make_application(FakeClient(), remote=remote)
        app.start()
        app.pair_device()
        gtk.drain_idle()

        code, on_copy, _on_new, _on_closed = gtk.pairing_dialogs[-1]
        self.assertEqual(code, "TEST-CODE-ONLY")
        on_copy()
        self.assertEqual(gtk.clipboard, ["TEST-CODE-ONLY"])
        self.assertEqual(gtk.pairing_views[-1].copied, 1)

        remote.claimed = True
        app._on_pairing_tick()
        gtk.drain_idle()
        self.assertEqual(gtk.pairing_views[-1].claimed, 1)

        remote.artifact = PairingArtifact("TEST-EXPIRED-CODE", expires_at=1)
        app.pair_device()
        gtk.drain_idle()
        app._on_pairing_tick()
        self.assertEqual(gtk.pairing_views[-1].expired, 1)


if __name__ == "__main__":
    unittest.main()
