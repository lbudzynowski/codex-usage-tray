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

    def configure_remote_control(
        self,
        on_start: Callable[[], None],
        on_stop: Callable[[], None],
        on_pair: Callable[[], None],
        on_prevent_sleep: Callable[[bool], None],
    ) -> None: ...

    def show_remote_control(
        self,
        status: str,
        *,
        start_enabled: bool,
        stop_enabled: bool,
        pair_enabled: bool,
        prevent_sleep: bool,
        inhibitor_active: bool,
        inhibitor_error: bool,
    ) -> None: ...


class PairingView(Protocol):
    def show_remaining(self, seconds: int | None) -> None: ...
    def show_copied(self) -> None: ...
    def show_claimed(self) -> None: ...
    def show_expired(self) -> None: ...
    def show_error(self) -> None: ...
    def close(self) -> None: ...


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

    def show_pairing_dialog(
        self,
        manual_code: str,
        *,
        on_copy: Callable[[], None],
        on_new: Callable[[], None],
        on_closed: Callable[[], None],
    ) -> PairingView: ...

    def show_pairing_error(self, message: str) -> None: ...

    def copy_to_clipboard(self, text: str) -> None: ...


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
        from gi.repository import Gdk, GLib, Gtk
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
            self._usage_status = ("Usage limits", ("Refreshing usage limits…",), "", False, None)
            self._remote_status: tuple[str, bool, bool, bool, bool, bool, bool] | None = None
            self._sign_in_item = Gtk.MenuItem(label="Sign in to Codex…")
            self._sign_in_item.connect("activate", lambda _item: on_sign_in())
            self._refresh_item = Gtk.MenuItem(label="Refresh")
            self._refresh_item.connect("activate", lambda _item: on_refresh())
            self._quit_item = Gtk.MenuItem(label="Quit")
            self._quit_item.connect("activate", lambda _item: on_quit())
            self._indicator.set_menu(self._menu)

        def configure_remote_control(
            self,
            on_start: Callable[[], None],
            on_stop: Callable[[], None],
            on_pair: Callable[[], None],
            on_prevent_sleep: Callable[[bool], None],
        ) -> None:
            self._remote_label = Gtk.MenuItem(label="Remote Control: Checking…")
            self._remote_label.set_sensitive(False)
            self._remote_inhibitor = Gtk.MenuItem(label="Sleep inhibitor: inactive")
            self._remote_inhibitor.set_sensitive(False)
            self._remote_start = Gtk.MenuItem(label="Start Remote Control")
            self._remote_start.connect("activate", lambda _item: on_start())
            self._remote_stop = Gtk.MenuItem(label="Stop Remote Control")
            self._remote_stop.connect("activate", lambda _item: on_stop())
            self._remote_pair = Gtk.MenuItem(label="Pair a device…")
            self._remote_pair.connect("activate", lambda _item: on_pair())
            self._prevent_sleep = Gtk.CheckMenuItem(
                label="Prevent sleep while Remote Control is active"
            )
            self._prevent_sleep.set_active(True)
            lid_warning = (
                "Lid-close behavior is controlled by the operating system; "
                "do not assume Remote Control will remain available. Never put "
                "the laptop in a bag while it remains powered and awake."
            )
            self._prevent_sleep.set_tooltip_text(lid_warning)
            self._remote_inhibitor.set_tooltip_text(lid_warning)
            self._prevent_sleep.connect(
                "toggled", lambda item: on_prevent_sleep(bool(item.get_active()))
            )
            self._remote_status = (
                "Remote Control: Checking…",
                False,
                False,
                False,
                True,
                False,
                False,
            )
            self._render()

        def show_status(
            self,
            title: str,
            lines: tuple[str, ...],
            label: str,
            *,
            sign_in_available: bool = False,
            account_info: str | None = None,
        ) -> None:
            self._usage_status = (
                title,
                lines,
                label,
                sign_in_available,
                account_info,
            )
            self._render()

        def show_remote_control(
            self,
            status: str,
            *,
            start_enabled: bool,
            stop_enabled: bool,
            pair_enabled: bool,
            prevent_sleep: bool,
            inhibitor_active: bool,
            inhibitor_error: bool,
        ) -> None:
            self._remote_status = (
                status,
                start_enabled,
                stop_enabled,
                pair_enabled,
                prevent_sleep,
                inhibitor_active,
                inhibitor_error,
            )
            self._render()

        def _render(self) -> None:
            title, lines, label, sign_in_available, account_info = self._usage_status
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
            if self._remote_status is not None:
                (
                    remote_status,
                    start_enabled,
                    stop_enabled,
                    pair_enabled,
                    prevent_sleep,
                    inhibitor_active,
                    inhibitor_error,
                ) = self._remote_status
                self._remote_label.set_label(remote_status)
                self._remote_start.set_sensitive(start_enabled)
                self._remote_stop.set_sensitive(stop_enabled)
                self._remote_pair.set_sensitive(pair_enabled)
                self._prevent_sleep.set_sensitive(
                    "Unavailable" not in remote_status
                )
                if self._prevent_sleep.get_active() != prevent_sleep:
                    self._prevent_sleep.set_active(prevent_sleep)
                inhibitor_label = (
                    "Sleep inhibitor: error"
                    if inhibitor_error
                    else (
                        "Sleep inhibitor: active (lid close is OS-controlled)"
                        if inhibitor_active
                        else "Sleep inhibitor: inactive"
                    )
                )
                self._remote_inhibitor.set_label(inhibitor_label)
                for item in (
                    self._remote_label,
                    self._remote_start,
                    self._remote_stop,
                    self._remote_pair,
                    self._prevent_sleep,
                    self._remote_inhibitor,
                    Gtk.SeparatorMenuItem(),
                ):
                    self._menu.append(item)
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

        def show_pairing_dialog(
            self,
            manual_code: str,
            *,
            on_copy: Callable[[], None],
            on_new: Callable[[], None],
            on_closed: Callable[[], None],
        ) -> PairingView:
            dialog = Gtk.Dialog(title="Pair a device", flags=Gtk.DialogFlags.MODAL)
            dialog.set_default_size(420, -1)
            content = dialog.get_content_area()
            content.set_spacing(10)
            content.set_border_width(16)
            instruction = Gtk.Label(
                label="Enter this code in the ChatGPT mobile app."
            )
            instruction.set_line_wrap(True)
            instruction.set_xalign(0)
            code_label = Gtk.Label()
            code_label.set_markup(
                f'<span size="xx-large" weight="bold">{GLib.markup_escape_text(manual_code)}</span>'
            )
            code_label.set_selectable(True)
            expiry_label = Gtk.Label(label="Checking expiry…")
            message_label = Gtk.Label(label="")
            copy_button = Gtk.Button(label="Copy code")
            copy_button.connect("clicked", lambda _button: on_copy())
            new_button = Gtk.Button(label="Generate new code")
            new_button.connect("clicked", lambda _button: on_new())
            button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            button_box.pack_start(copy_button, False, False, 0)
            button_box.pack_start(new_button, False, False, 0)
            for widget in (
                instruction,
                code_label,
                expiry_label,
                message_label,
                button_box,
            ):
                content.pack_start(widget, False, False, 0)
            dialog.add_button("Close", Gtk.ResponseType.CLOSE)

            class _PairingDialog:
                def __init__(self) -> None:
                    self._closed = False

                def show_remaining(self, seconds: int | None) -> None:
                    if seconds is None:
                        expiry_label.set_text("Expiry time unavailable")
                    else:
                        minutes, remaining_seconds = divmod(seconds, 60)
                        expiry_label.set_text(
                            f"Expires in {minutes:02d}:{remaining_seconds:02d}"
                        )

                def show_copied(self) -> None:
                    message_label.set_text("Pairing code copied.")

                def show_claimed(self) -> None:
                    code_label.set_text("")
                    expiry_label.set_text("")
                    message_label.set_text("Device paired successfully.")
                    copy_button.set_sensitive(False)

                def show_expired(self) -> None:
                    code_label.set_text("")
                    expiry_label.set_text("")
                    message_label.set_text("This pairing code has expired.")
                    copy_button.set_sensitive(False)

                def show_error(self) -> None:
                    code_label.set_text("")
                    expiry_label.set_text("")
                    message_label.set_text("Could not check the pairing status.")
                    copy_button.set_sensitive(False)

                def close(self) -> None:
                    if not self._closed:
                        dialog.destroy()

                def _closed_callback(self) -> None:
                    if self._closed:
                        return
                    self._closed = True
                    code_label.set_text("")
                    on_closed()

            view = _PairingDialog()
            dialog.connect("response", lambda _dialog, _response: view.close())
            dialog.connect("destroy", lambda _dialog: view._closed_callback())
            dialog.show_all()
            return view

        def show_pairing_error(self, message: str) -> None:
            dialog = Gtk.MessageDialog(
                None,
                Gtk.DialogFlags.MODAL,
                Gtk.MessageType.ERROR,
                Gtk.ButtonsType.CLOSE,
                message,
            )
            try:
                dialog.run()
            finally:
                dialog.destroy()

        def copy_to_clipboard(self, text: str) -> None:
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            clipboard.set_text(text, -1)

        def create_tray(
            self, on_refresh: Callable[[], None], on_quit: Callable[[], None], on_sign_in: Callable[[], None]
        ) -> TrayView:
            return _GtkTray(on_refresh, on_quit, on_sign_in)

    return _Adapter()
