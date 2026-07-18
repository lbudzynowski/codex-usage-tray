"""GTK 3 tray widgets, isolated from the application coordinator.

The GI imports intentionally live in :func:`load_gtk_adapter` so importing this
package remains possible on development and test systems without GTK installed.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol


class TrayView(Protocol):
    """Small GTK-facing surface used by the application coordinator."""

    def show_status(
        self,
        title: str,
        lines: tuple[str, ...],
        label: str,
        *,
        sign_in_available: bool = False,
        account_info: str | None = None,
    ) -> None: ...


class GtkAdapter(Protocol):
    """GTK main-loop and indicator factory operations required at runtime."""

    def idle_add(self, callback: Callable[[], object]) -> int: ...

    def timeout_add(self, milliseconds: int, callback: Callable[[], bool]) -> int: ...

    def source_remove(self, source_id: int) -> bool: ...

    def main(self) -> None: ...

    def main_quit(self) -> None: ...

    def create_tray(
        self,
        on_refresh: Callable[[], None],
        on_quit: Callable[[], None],
        on_sign_in: Callable[[], None],
    ) -> TrayView: ...

    def show_sign_in_dialog(self) -> bool: ...


def load_gtk_adapter() -> GtkAdapter:
    """Load Ubuntu's GTK 3 and available AppIndicator namespace at runtime."""

    try:
        import gi

        gi.require_version("Gtk", "3.0")
        try:
            gi.require_version("AyatanaAppIndicator3", "0.1")
            from gi.repository import AyatanaAppIndicator3 as AppIndicator
        except (ImportError, ValueError):
            gi.require_version("AppIndicator3", "0.1")
            from gi.repository import AppIndicator3 as AppIndicator
        from gi.repository import GLib, Gtk
    except (ImportError, ValueError) as error:
        raise RuntimeError(
            "GTK 3 and an Ubuntu AppIndicator binding are required to run the tray"
        ) from error

    class _GtkTray:
        def __init__(
            self, on_refresh: Callable[[], None], on_quit: Callable[[], None], on_sign_in: Callable[[], None]
        ) -> None:
            self._indicator = AppIndicator.Indicator.new(
                "codex-usage-tray", "utilities-system-monitor-symbolic",
                AppIndicator.IndicatorCategory.APPLICATION_STATUS,
            )
            self._indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            self._menu = Gtk.Menu()
            self._status_items: list[object] = []
            self._sign_in_item = Gtk.MenuItem(label="Sign in to Codex…")
            self._sign_in_item.connect("activate", lambda _item: on_sign_in())
            self._refresh_item = Gtk.MenuItem(label="Refresh")
            self._refresh_item.connect("activate", lambda _item: on_refresh())
            self._quit_item = Gtk.MenuItem(label="Quit")
            self._quit_item.connect("activate", lambda _item: on_quit())
            self._indicator.set_menu(self._menu)

        def show_status(
            self,
            title: str,
            lines: tuple[str, ...],
            label: str,
            *,
            sign_in_available: bool = False,
            account_info: str | None = None,
        ) -> None:
            for item in self._menu.get_children():
                self._menu.remove(item)
            self._status_items = []
            for text in (title, *((account_info,) if account_info else ()), *lines):
                item = Gtk.MenuItem(label=text)
                item.set_sensitive(False)
                self._menu.append(item)
                self._status_items.append(item)
            separator = Gtk.SeparatorMenuItem()
            self._menu.append(separator)
            self._status_items.append(separator)
            if sign_in_available:
                self._sign_in_item.set_sensitive(True)
                self._menu.append(self._sign_in_item)
            self._menu.append(self._refresh_item)
            self._menu.append(self._quit_item)
            self._indicator.set_label(label, "")
            self._menu.show_all()

    class _Adapter:
        def idle_add(self, callback: Callable[[], object]) -> int:
            return GLib.idle_add(callback)

        def timeout_add(self, milliseconds: int, callback: Callable[[], bool]) -> int:
            return GLib.timeout_add(milliseconds, callback)

        def source_remove(self, source_id: int) -> bool:
            return GLib.source_remove(source_id)

        def main(self) -> None:
            Gtk.main()

        def main_quit(self) -> None:
            Gtk.main_quit()

        def show_sign_in_dialog(self) -> bool:
            dialog = Gtk.MessageDialog(
                None,
                0,
                Gtk.MessageType.INFO,
                Gtk.ButtonsType.NONE,
                "Codex Usage Tray needs Codex to be signed in before it can read usage limits.",
            )
            dialog.set_title("Codex sign-in required")
            dialog.add_button("Not now", Gtk.ResponseType.CANCEL)
            dialog.add_button("Sign in", Gtk.ResponseType.OK)
            try:
                return dialog.run() == Gtk.ResponseType.OK
            finally:
                dialog.destroy()

        def create_tray(
            self, on_refresh: Callable[[], None], on_quit: Callable[[], None], on_sign_in: Callable[[], None]
        ) -> TrayView:
            return _GtkTray(on_refresh, on_quit, on_sign_in)

    return _Adapter()
