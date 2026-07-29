"""Application coordination for the Codex Usage Tray."""

from __future__ import annotations

import webbrowser
from collections.abc import Callable
from datetime import tzinfo
from threading import Lock, Thread
from typing import Protocol

from .codex_client import (
    CodexAccountState,
    CodexAppServerClient,
    CodexClientError,
    CodexLoginCompletion,
)
from .pairing import PairingArtifact, PairingSession, PairingState
from .presentation import RateLimitPresentation, present_rate_limits
from .remote_control import (
    RemoteControlError,
    RemoteControlPhase,
    RemoteControlState,
    RemoteControlUnavailableError,
)
from .sleep_inhibitor import InhibitorError
from .tray import GtkAdapter, PairingView, TrayView

REFRESH_INTERVAL_SECONDS = 300
LOGIN_POLL_INTERVAL_MS = 1000
REMOTE_REFRESH_INTERVAL_MS = 15_000
PAIRING_POLL_INTERVAL_MS = 1_000
UNAVAILABLE_MESSAGE = "Usage unavailable."
REFRESHING_MESSAGE = "Refreshing usage limits…"
SIGNED_OUT_MESSAGE = "Codex is not signed in."
LOGIN_PENDING_MESSAGE = "Complete sign-in in your browser…"
LOGIN_FAILED_MESSAGE = "Codex sign-in failed."
BROWSER_FAILED_MESSAGE = "Could not open the browser. Run `codex login` in a terminal."


class RateLimitClient(Protocol):
    def start(self) -> None: ...
    def read_account(self) -> CodexAccountState: ...
    def read_rate_limits(self) -> tuple: ...
    def start_chatgpt_login(self): ...
    def pop_login_completion(self) -> CodexLoginCompletion | None: ...
    def close(self) -> None: ...


class RemoteClient(Protocol):
    def read_status(self) -> RemoteControlState: ...
    def start(self) -> RemoteControlState: ...
    def stop(self) -> RemoteControlState: ...
    def pair(self) -> PairingArtifact: ...
    def pairing_claimed(self, artifact: PairingArtifact) -> bool: ...


class SleepController(Protocol):
    @property
    def is_active(self) -> bool: ...
    def set_required(self, required: bool) -> None: ...
    def close(self) -> None: ...


WorkerLauncher = Callable[[Callable[[], None]], None]
ClientFactory = Callable[[], RateLimitClient]
UrlOpener = Callable[[str], bool]


def launch_worker(callback: Callable[[], None]) -> None:
    """Run blocking Codex communication on a daemon worker thread."""

    Thread(target=callback, name="codex-usage-worker", daemon=True).start()


def default_url_opener(url: str) -> bool:
    return webbrowser.open(url, new=2)


class TrayApplication:
    """Coordinate refreshes while keeping all GTK mutations on its main loop."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = CodexAppServerClient,
        gtk: GtkAdapter,
        local_timezone: tzinfo,
        worker_launcher: WorkerLauncher = launch_worker,
        url_opener: UrlOpener = default_url_opener,
        remote_control: RemoteClient | None = None,
        sleep_inhibitor: SleepController | None = None,
    ) -> None:
        self._client_factory = client_factory
        self._client: RateLimitClient | None = None
        self._gtk = gtk
        self._local_timezone = local_timezone
        self._worker_launcher = worker_launcher
        self._url_opener = url_opener
        self._remote_control = remote_control
        self._sleep_inhibitor = sleep_inhibitor
        self._tray: TrayView = gtk.create_tray(self.refresh, self.quit, self.sign_in)
        self._timer_id: int | None = None
        self._login_timer_id: int | None = None
        self._remote_timer_id: int | None = None
        self._pairing_timer_id: int | None = None
        self._client_started = False
        self._closed = False
        self._refreshing = False
        self._login_pending = False
        self._pending_login_id: str | None = None
        self._prompt_seen = False
        self._remote_refreshing = False
        self._remote_generation = 0
        self._remote_state = RemoteControlState(RemoteControlPhase.CHECKING)
        # Default to protecting an active Remote Control connection. This is a
        # per-process preference; the user can turn it off from the tray menu.
        self._prevent_sleep = True
        self._inhibitor_active = False
        self._inhibitor_error = False
        self._pairing_session: PairingSession | None = None
        self._pairing_view: PairingView | None = None
        self._pairing_polling = False
        self._lock = Lock()
        self._inhibitor_sync_lock = Lock()
        if remote_control is not None and hasattr(self._tray, "configure_remote_control"):
            self._tray.configure_remote_control(
                self.start_remote_control,
                self.stop_remote_control,
                self.pair_device,
                self.set_prevent_sleep,
            )

    def run(self) -> None:
        self.start()
        self._gtk.main()

    def start(self) -> None:
        with self._lock:
            if self._closed or self._timer_id is not None:
                return
            self._timer_id = self._gtk.timeout_add(REFRESH_INTERVAL_SECONDS * 1000, self._on_periodic_refresh)
            self._login_timer_id = self._gtk.timeout_add(LOGIN_POLL_INTERVAL_MS, self._on_login_poll)
            if self._remote_control is not None:
                self._remote_timer_id = self._gtk.timeout_add(
                    REMOTE_REFRESH_INTERVAL_MS, self._on_remote_refresh
                )
        self.refresh()
        self.refresh_remote_control()

    def refresh(self) -> None:
        with self._lock:
            if self._closed or self._refreshing:
                return
            self._refreshing = True
        self._gtk.idle_add(self._show_refreshing)
        self._worker_launcher(self._refresh_worker)

    def sign_in(self) -> None:
        with self._lock:
            if self._closed or self._login_pending:
                return
            self._login_pending = True
        self._gtk.idle_add(lambda: self._show_message(LOGIN_PENDING_MESSAGE))
        self._worker_launcher(self._login_worker)

    def refresh_remote_control(self) -> None:
        if self._remote_control is None:
            return
        with self._lock:
            if self._closed or self._remote_refreshing:
                return
            self._remote_refreshing = True
            generation = self._remote_generation
        self._worker_launcher(lambda: self._remote_status_worker(generation))

    def start_remote_control(self) -> None:
        if self._remote_control is None:
            return
        with self._lock:
            self._remote_generation += 1
            generation = self._remote_generation
        self._set_remote_state(RemoteControlState(RemoteControlPhase.STARTING))
        self._worker_launcher(
            lambda: self._remote_action_worker("start", generation)
        )

    def stop_remote_control(self) -> None:
        if self._remote_control is None:
            return
        with self._lock:
            self._remote_generation += 1
            generation = self._remote_generation
        self._worker_launcher(
            lambda: self._remote_action_worker("stop", generation)
        )

    def pair_device(self) -> None:
        if self._remote_control is None:
            return
        self._worker_launcher(self._pair_worker)

    def set_prevent_sleep(self, enabled: bool) -> None:
        with self._lock:
            self._prevent_sleep = bool(enabled)
        self._worker_launcher(self._sync_inhibitor_worker)

    def quit(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            timer_id = self._timer_id
            login_timer_id = self._login_timer_id
            remote_timer_id = self._remote_timer_id
            pairing_timer_id = self._pairing_timer_id
            self._timer_id = None
            self._login_timer_id = None
            self._remote_timer_id = None
            self._pairing_timer_id = None
            client = self._client
            self._client = None
            self._pairing_session = None
            pairing_view = self._pairing_view
            self._pairing_view = None
        if timer_id is not None:
            self._gtk.source_remove(timer_id)
        if login_timer_id is not None:
            self._gtk.source_remove(login_timer_id)
        if remote_timer_id is not None:
            self._gtk.source_remove(remote_timer_id)
        if pairing_timer_id is not None:
            self._gtk.source_remove(pairing_timer_id)
        if pairing_view is not None:
            pairing_view.close()
        self._worker_launcher(lambda: self._close_worker(client))

    def _on_periodic_refresh(self) -> bool:
        self.refresh()
        with self._lock:
            return not self._closed

    def _on_login_poll(self) -> bool:
        self._worker_launcher(self._login_completion_worker)
        with self._lock:
            return not self._closed

    def _on_remote_refresh(self) -> bool:
        self.refresh_remote_control()
        with self._lock:
            return not self._closed

    def _on_pairing_tick(self) -> bool:
        with self._lock:
            session = self._pairing_session
            view = self._pairing_view
            closed = self._closed
            polling = self._pairing_polling
        if closed or session is None or view is None:
            return False
        remaining = session.tick()
        if session.state is PairingState.EXPIRED:
            view.show_expired()
            self._clear_pairing_session(keep_view=True)
            return False
        view.show_remaining(remaining)
        if not polling:
            with self._lock:
                self._pairing_polling = True
            self._worker_launcher(self._pairing_status_worker)
        return True

    def _refresh_worker(self) -> None:
        client = None
        try:
            client = self._ensure_started_client()
            if client is None:
                return
            account_state = client.read_account()
            if account_state.is_signed_out:
                self._gtk.idle_add(self._show_signed_out)
                return
            account = account_state.account
            snapshots = client.read_rate_limits()
            presentation = present_rate_limits(snapshots, self._local_timezone)
        except CodexClientError:
            if client is None:
                with self._lock:
                    client = self._client
            if client is not None:
                self._discard_failed_client(client)
            self._gtk.idle_add(self._show_unavailable)
        except (OSError, RuntimeError, ValueError):
            self._gtk.idle_add(self._show_unavailable)
        else:
            self._gtk.idle_add(lambda: self._show_presentation(presentation, account_state))
        finally:
            with self._lock:
                self._refreshing = False

    def _login_worker(self) -> None:
        try:
            client = self._ensure_started_client()
            if client is None:
                return
            login = client.start_chatgpt_login()
            with self._lock:
                self._pending_login_id = login.login_id
            try:
                opened = bool(self._url_opener(login.auth_url))
            except Exception:
                opened = False
            if not opened:
                with self._lock:
                    self._login_pending = False
                    self._pending_login_id = None
                self._gtk.idle_add(lambda: self._show_message(BROWSER_FAILED_MESSAGE, sign_in_available=True))
        except CodexClientError:
            with self._lock:
                self._login_pending = False
                self._pending_login_id = None
            self._gtk.idle_add(lambda: self._show_message(LOGIN_FAILED_MESSAGE, sign_in_available=True))

    def _login_completion_worker(self) -> None:
        with self._lock:
            client = self._client
            pending_id = self._pending_login_id
        if client is None or pending_id is None:
            return
        completion = client.pop_login_completion()
        if completion is None or completion.login_id != pending_id:
            return
        if completion.success:
            with self._lock:
                self._login_pending = False
                self._pending_login_id = None
            self.refresh()
        else:
            with self._lock:
                self._login_pending = False
                self._pending_login_id = None
            self._gtk.idle_add(lambda: self._show_message(LOGIN_FAILED_MESSAGE, sign_in_available=True))

    def _remote_status_worker(self, generation: int) -> None:
        assert self._remote_control is not None
        try:
            state = self._remote_control.read_status()
        except RemoteControlUnavailableError:
            state = RemoteControlState(RemoteControlPhase.UNAVAILABLE)
        except RemoteControlError:
            state = RemoteControlState(RemoteControlPhase.ERROR)
        finally:
            with self._lock:
                self._remote_refreshing = False
                stale = generation != self._remote_generation
        if stale:
            return
        self._set_remote_state(state)
        self._sync_inhibitor_worker()

    def _remote_action_worker(self, action: str, generation: int) -> None:
        assert self._remote_control is not None
        try:
            operation = (
                self._remote_control.start
                if action == "start"
                else self._remote_control.stop
            )
            state = operation()
        except RemoteControlUnavailableError:
            state = RemoteControlState(RemoteControlPhase.UNAVAILABLE)
        except RemoteControlError:
            state = RemoteControlState(RemoteControlPhase.ERROR)
        with self._lock:
            stale = generation != self._remote_generation
        if stale:
            return
        self._set_remote_state(state)
        self._sync_inhibitor_worker()

    def _pair_worker(self) -> None:
        assert self._remote_control is not None
        try:
            artifact = self._remote_control.pair()
        except RemoteControlError:
            self._gtk.idle_add(
                lambda: self._gtk.show_pairing_error(
                    "Could not create a pairing code. Try again."
                )
            )
            return
        self._gtk.idle_add(lambda: self._show_pairing(artifact))

    def _pairing_status_worker(self) -> None:
        with self._lock:
            session = self._pairing_session
        if session is None or self._remote_control is None:
            with self._lock:
                self._pairing_polling = False
            return
        try:
            claimed = self._remote_control.pairing_claimed(session.artifact)
        except RemoteControlError:
            claimed = False
            failed = True
        else:
            failed = False
        with self._lock:
            self._pairing_polling = False
        if failed:
            self._gtk.idle_add(self._show_pairing_status_error)
        elif claimed:
            self._gtk.idle_add(self._show_pairing_claimed)

    def _sync_inhibitor_worker(self) -> None:
        inhibitor = self._sleep_inhibitor
        if inhibitor is None:
            return
        with self._inhibitor_sync_lock:
            with self._lock:
                required = (
                    self._prevent_sleep
                    and self._remote_state.is_active
                    and not self._closed
                )
            try:
                inhibitor.set_required(required)
            except InhibitorError:
                inhibitor_error = True
            else:
                inhibitor_error = False
            try:
                inhibitor_active = bool(inhibitor.is_active)
            except (InhibitorError, OSError, RuntimeError):
                inhibitor_active = False
                inhibitor_error = True
            with self._lock:
                self._inhibitor_active = inhibitor_active
                self._inhibitor_error = inhibitor_error
        self._gtk.idle_add(self._show_remote_control)

    def _ensure_started_client(self) -> RateLimitClient | None:
        with self._lock:
            if self._closed:
                return None
            if self._client is None:
                self._client = self._client_factory()
                self._client_started = False
            client = self._client
        if not self._client_started:
            client.start()
            self._client_started = True
        return client

    def _discard_failed_client(self, failed_client: RateLimitClient) -> None:
        with self._lock:
            if self._client is failed_client:
                self._client = None
                self._client_started = False
                self._login_pending = False
                self._pending_login_id = None
        try:
            failed_client.close()
        except (CodexClientError, OSError, RuntimeError):
            pass

    def _close_worker(self, client: RateLimitClient | None) -> None:
        try:
            if client is not None:
                client.close()
        except (CodexClientError, OSError, RuntimeError):
            pass
        inhibitor = self._sleep_inhibitor
        inhibitor_active = False
        inhibitor_error = False
        if inhibitor is not None:
            with self._inhibitor_sync_lock:
                try:
                    inhibitor.close()
                except (InhibitorError, OSError, RuntimeError):
                    inhibitor_error = True
                    try:
                        inhibitor_active = bool(inhibitor.is_active)
                    except (InhibitorError, OSError, RuntimeError):
                        inhibitor_active = False
        with self._lock:
            self._inhibitor_active = inhibitor_active
            self._inhibitor_error = inhibitor_error
        self._gtk.idle_add(self._gtk.main_quit)

    def _set_remote_state(self, state: RemoteControlState) -> None:
        with self._lock:
            self._remote_state = state
        self._gtk.idle_add(self._show_remote_control)

    def _show_remote_control(self) -> bool:
        if self._remote_control is None:
            return False
        with self._lock:
            state = self._remote_state
            prevent_sleep = self._prevent_sleep
            inhibitor_active = self._inhibitor_active
            inhibitor_error = self._inhibitor_error
        self._tray.show_remote_control(
            _format_remote_state(state),
            start_enabled=state.phase
            in (
                RemoteControlPhase.STOPPED,
                RemoteControlPhase.DISCONNECTED,
                RemoteControlPhase.ERROR,
            ),
            stop_enabled=state.phase
            in (
                RemoteControlPhase.STARTING,
                RemoteControlPhase.CONNECTED,
                RemoteControlPhase.DISCONNECTED,
                RemoteControlPhase.ERROR,
            ),
            pair_enabled=state.phase is RemoteControlPhase.CONNECTED,
            prevent_sleep=prevent_sleep,
            inhibitor_active=inhibitor_active,
            inhibitor_error=inhibitor_error,
        )
        return False

    def _show_pairing(self, artifact: PairingArtifact) -> bool:
        if self._closed:
            return False
        if self._pairing_view is not None:
            self._pairing_view.close()
        session = PairingSession(artifact)
        view = self._gtk.show_pairing_dialog(
            artifact.manual_code,
            on_copy=self._copy_pairing_code,
            on_new=self._new_pairing_code,
            on_closed=self._pairing_closed,
        )
        with self._lock:
            self._pairing_session = session
            self._pairing_view = view
            self._pairing_timer_id = self._gtk.timeout_add(
                PAIRING_POLL_INTERVAL_MS, self._on_pairing_tick
            )
        view.show_remaining(session.tick())
        return False

    def _copy_pairing_code(self) -> None:
        with self._lock:
            session = self._pairing_session
            view = self._pairing_view
        if session is None or view is None:
            return
        session.copy(self._gtk.copy_to_clipboard)
        if session.state is PairingState.COPIED:
            view.show_copied()

    def _new_pairing_code(self) -> None:
        self._clear_pairing_session(keep_view=False)
        self.pair_device()

    def _pairing_closed(self) -> None:
        self._clear_pairing_session(keep_view=False, close_view=False)

    def _show_pairing_claimed(self) -> bool:
        with self._lock:
            session = self._pairing_session
            view = self._pairing_view
        if session is not None and view is not None:
            session.mark_claimed()
            view.show_claimed()
            self._clear_pairing_session(keep_view=True)
        return False

    def _show_pairing_status_error(self) -> bool:
        with self._lock:
            session = self._pairing_session
            view = self._pairing_view
        if session is not None and view is not None:
            session.mark_error()
            view.show_error()
            self._clear_pairing_session(keep_view=True)
        return False

    def _clear_pairing_session(
        self, *, keep_view: bool, close_view: bool = True
    ) -> None:
        with self._lock:
            timer_id = self._pairing_timer_id
            view = self._pairing_view
            self._pairing_timer_id = None
            self._pairing_session = None
            self._pairing_polling = False
            if not keep_view:
                self._pairing_view = None
        if timer_id is not None:
            self._gtk.source_remove(timer_id)
        if not keep_view and close_view and view is not None:
            view.close()

    def _show_refreshing(self) -> bool:
        self._tray.show_status("Usage limits", (REFRESHING_MESSAGE,), "", sign_in_available=False, account_info=None)
        return False

    def _show_unavailable(self) -> bool:
        self._tray.show_status("Usage limits", (UNAVAILABLE_MESSAGE,), "", sign_in_available=False, account_info=None)
        return False

    def _show_message(self, message: str, sign_in_available: bool = False) -> bool:
        self._tray.show_status("Usage limits", (message,), "", sign_in_available=sign_in_available, account_info=None)
        return False

    def _show_signed_out(self) -> bool:
        self._tray.show_status("Usage limits", (SIGNED_OUT_MESSAGE,), "", sign_in_available=True, account_info=None)
        if not self._prompt_seen:
            self._prompt_seen = True
            if self._gtk.show_sign_in_dialog():
                self.sign_in()
        return False

    def _show_presentation(self, presentation: RateLimitPresentation, account_state: CodexAccountState) -> bool:
        if presentation.empty_message is not None:
            lines = (presentation.empty_message,)
            label = ""
        else:
            lines = tuple(
                f"{window.duration_label}: {window.remaining_percent_text} remaining"
                + (f" — resets {window.reset_time_text}" if window.reset_time_text is not None else "")
                for window in presentation.windows
            )
            label = min((window.remaining_percent_text for window in presentation.windows), key=lambda value: float(value.removesuffix("%")))
        self._tray.show_status(presentation.title, lines, label, sign_in_available=False, account_info=_format_account_info(account_state))
        return False


def _format_account_info(account_state: CodexAccountState) -> str | None:
    account = account_state.account
    if account is None:
        return None
    plan = account.plan_type.title() if account.plan_type else None
    if account.email and plan:
        return f"Account: {account.email} — {plan}"
    if account.email:
        return f"Account: {account.email}"
    if plan:
        return f"Account plan: {plan}"
    return f"Account type: {account.account_type}"


def _format_remote_state(state: RemoteControlState) -> str:
    labels = {
        RemoteControlPhase.CHECKING: "Remote Control: Checking…",
        RemoteControlPhase.STOPPED: "Remote Control: Stopped",
        RemoteControlPhase.STARTING: "Remote Control: Starting…",
        RemoteControlPhase.CONNECTED: "Remote Control: Connected",
        RemoteControlPhase.DISCONNECTED: "Remote Control: Disconnected",
        RemoteControlPhase.ERROR: "Remote Control: Error",
        RemoteControlPhase.UNAVAILABLE: "Remote Control: Unavailable",
    }
    label = labels[state.phase]
    if state.server_name:
        return f"{label} — {state.server_name}"
    return label
