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
from .presentation import RateLimitPresentation, present_rate_limits
from .tray import GtkAdapter, TrayView

REFRESH_INTERVAL_SECONDS = 300
LOGIN_POLL_INTERVAL_MS = 1000
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
    ) -> None:
        self._client_factory = client_factory
        self._client: RateLimitClient | None = None
        self._gtk = gtk
        self._local_timezone = local_timezone
        self._worker_launcher = worker_launcher
        self._url_opener = url_opener
        self._tray: TrayView = gtk.create_tray(self.refresh, self.quit, self.sign_in)
        self._timer_id: int | None = None
        self._login_timer_id: int | None = None
        self._client_started = False
        self._closed = False
        self._refreshing = False
        self._login_pending = False
        self._pending_login_id: str | None = None
        self._prompt_seen = False
        self._lock = Lock()

    def run(self) -> None:
        self.start()
        self._gtk.main()

    def start(self) -> None:
        with self._lock:
            if self._closed or self._timer_id is not None:
                return
            self._timer_id = self._gtk.timeout_add(REFRESH_INTERVAL_SECONDS * 1000, self._on_periodic_refresh)
            self._login_timer_id = self._gtk.timeout_add(LOGIN_POLL_INTERVAL_MS, self._on_login_poll)
        self.refresh()

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

    def quit(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            timer_id = self._timer_id
            login_timer_id = self._login_timer_id
            self._timer_id = None
            self._login_timer_id = None
            client = self._client
            self._client = None
        if timer_id is not None:
            self._gtk.source_remove(timer_id)
        if login_timer_id is not None:
            self._gtk.source_remove(login_timer_id)
        self._worker_launcher(lambda: self._close_worker(client))

    def _on_periodic_refresh(self) -> bool:
        self.refresh()
        with self._lock:
            return not self._closed

    def _on_login_poll(self) -> bool:
        self._worker_launcher(self._login_completion_worker)
        with self._lock:
            return not self._closed

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
        finally:
            self._gtk.idle_add(self._gtk.main_quit)

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
