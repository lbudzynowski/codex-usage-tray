# Codex Usage Tray

Unofficial Ubuntu GNOME tray indicator for OpenAI Codex usage limits.

Codex Usage Tray reads rate-limit information from the local Codex app-server
and displays the remaining allowance in the top panel.

## Requirements

- Ubuntu GNOME with AppIndicator support
- Python 3.11 or newer
- OpenAI Codex installed and authenticated

Codex itself is not included in the package.

## Installation

Download the latest `.deb` package from GitHub Releases and install it with:

    sudo apt install ./codex-usage-tray_0.1.0_all.deb

Start the application immediately with:

    codex-usage-tray

The application is also configured to start automatically after the next login.

## Uninstallation

    sudo apt remove codex-usage-tray

## Development

Run the test suite:

    make test

Build the Debian package:

    ./scripts/build-deb.sh

The generated package is written to the `dist/` directory.

## License

MIT License. See [LICENSE](LICENSE).
