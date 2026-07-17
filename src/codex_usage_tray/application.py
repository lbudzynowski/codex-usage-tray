"""Application coordination for the Codex Usage Tray."""

from __future__ import annotations

from collections.abc import Callable
from datetime import tzinfo
from threading import Lock, Thread
from typing import Protocol

from .codex_client import CodexAppServerClient, CodexClientError
from .presentation import RateLimitPresentation, present_rate_limits
from .tray import GtkAdapter, TrayView

REFRESH_INTERVAL_SECONDS = 300
UNAVAILABLE_MESSAGE = "Usage unavailable."
REFRESHING_MESSAGE = "Refreshing usage limits…"


class RateLimitClient(Protocol):
    def start(self) -> None: ...

    def read_rate_limits(self) -> tuple: ...

    def close(self) -> None: ...


type WorkerLauncher = Callable[[Callable[[], None]], None]
type ClientFactory = Callable[[], RateLimitClient]


def launch_worker(callback: Callable[[], None]) -> None:
    """Run blocking Codex communication on a daemon worker thread."""

    Thread(target=callback, name="codex-usage-refresh", daemon=True).start()


class TrayApplication:
    """Coordinate refreshes while keeping all GTK mutations on its main loop."""

    def __init__(
        self,
        *,
        client_factory: ClientFactory = CodexAppServerClient,
        gtk: GtkAdapter,
        local_timezone: tzinfo,
        worker_launcher: WorkerLauncher = launch_worker,
    ) -> None:
        self._client_factory = client_factory
        self._client: RateLimitClient | None = None
        self._gtk = gtk
        self._local_timezone = local_timezone
        self._worker_launcher = worker_launcher
        self._tray: TrayView = gtk.create_tray(self.refresh, self.quit)
        self._timer_id: int | None = None
        self._client_started = False
        self._closed = False
        self._refreshing = False
        self._lock = Lock()

    def run(self) -> None:
        """Schedule the first refresh and enter GTK's main loop."""

        self.start()
        self._gtk.main()

    def start(self) -> None:
        """Schedule periodic updates and immediately request the first refresh."""

        with self._lock:
            if self._closed or self._timer_id is not None:
                return
            self._timer_id = self._gtk.timeout_add(
                REFRESH_INTERVAL_SECONDS * 1000, self._on_periodic_refresh
            )
        self.refresh()

    def refresh(self) -> None:
        """Request a refresh unless one is already in progress."""

        with self._lock:
            if self._closed or self._refreshing:
                return
            self._refreshing = True
        self._gtk.idle_add(self._show_refreshing)
        self._worker_launcher(self._refresh_worker)

    def quit(self) -> None:
        """Stop scheduling, close the client off-loop, then leave GTK cleanly."""

        with self._lock:
            if self._closed:
                return
            self._closed = True
            timer_id = self._timer_id
            self._timer_id = None
            client = self._client
            self._client = None
        if timer_id is not None:
            self._gtk.source_remove(timer_id)
        self._worker_launcher(lambda: self._close_worker(client))

    def _on_periodic_refresh(self) -> bool:
        self.refresh()
        with self._lock:
            return not self._closed

    def _refresh_worker(self) -> None:
        client: RateLimitClient | None = None
        try:
            client = self._get_or_create_client()
            if client is None:
                return
            if not self._client_started:
                client.start()
                self._client_started = True
            snapshots = client.read_rate_limits()
            presentation = present_rate_limits(snapshots, self._local_timezone)
        except CodexClientError:
            if client is not None:
                self._discard_failed_client(client)
            self._gtk.idle_add(self._show_unavailable)
        except (OSError, RuntimeError, ValueError):
            self._gtk.idle_add(self._show_unavailable)
        else:
            self._gtk.idle_add(lambda: self._show_presentation(presentation))
        finally:
            with self._lock:
                self._refreshing = False

    def _get_or_create_client(self) -> RateLimitClient | None:
        """Return the healthy client or construct the next replacement off-loop."""

        with self._lock:
            if self._closed:
                return None
            if self._client is None:
                self._client = self._client_factory()
                self._client_started = False
            return self._client

    def _discard_failed_client(self, failed_client: RateLimitClient) -> None:
        """Close and forget a broken client so the next refresh can replace it."""

        with self._lock:
            if self._client is failed_client:
                self._client = None
                self._client_started = False
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
        self._tray.show_status("Usage limits", (REFRESHING_MESSAGE,), "")
        return False

    def _show_unavailable(self) -> bool:
        self._tray.show_status("Usage limits", (UNAVAILABLE_MESSAGE,), "")
        return False

    def _show_presentation(self, presentation: RateLimitPresentation) -> bool:
        if presentation.empty_message is not None:
            lines = (presentation.empty_message,)
            label = ""
        else:
            lines = tuple(
                f"{window.duration_label}: {window.remaining_percent_text} remaining"
                + (
                    f" — resets {window.reset_time_text}"
                    if window.reset_time_text is not None
                    else ""
                )
                for window in presentation.windows
            )
            label = min(
                (window.remaining_percent_text for window in presentation.windows),
                key=lambda value: float(value.removesuffix("%")),
            )
        self._tray.show_status(presentation.title, lines, label)
        return False
