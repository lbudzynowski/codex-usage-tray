"""Launch the Ubuntu GNOME Codex Usage Tray indicator."""

from datetime import datetime

from .application import TrayApplication
from .remote_control import RemoteControlClient
from .sleep_inhibitor import SleepInhibitor
from .tray import load_gtk_adapter


def main() -> None:
    """Create the GTK adapter only when the executable is launched."""

    TrayApplication(
        gtk=load_gtk_adapter(),
        local_timezone=datetime.now().astimezone().tzinfo,
        remote_control=RemoteControlClient(),
        sleep_inhibitor=SleepInhibitor(),
    ).run()


if __name__ == "__main__":
    main()
