# Codex Usage Tray

Unofficial Ubuntu GNOME tray indicator for OpenAI Codex usage limits.

Codex Usage Tray reads rate-limit information from the local Codex app-server
and displays the remaining allowance in the top panel. It also checks Codex
account state before requesting usage limits, so new users see a clear sign-in
message instead of a generic unavailable state.

## Requirements

- Ubuntu GNOME with AppIndicator support
- Python 3.11 or newer
- OpenAI Codex installed separately

Codex itself is not bundled in this package. The package never contains the
developer's credentials, account tokens, browser cookies, or any bundled
credential material. Each operating-system user uses their own local Codex
authentication managed by Codex.

## Authentication

Codex Usage Tray does not implement authentication itself and does not read
`~/.codex/auth.json`. Codex continues to own authentication and token storage.

When Codex is signed out, the tray displays:

    Codex is not signed in.

and offers a **Sign in to Codex…** menu action. Choosing that action uses the
official Codex-managed ChatGPT browser flow through the local Codex app-server.
You may alternatively run this in a terminal:

    codex login

After successful authentication, Codex Usage Tray automatically reads the
account again and refreshes usage limits without requiring a restart.

## Installation

Download the latest `.deb` package from GitHub Releases and install it with:

    sudo apt install ./codex-usage-tray_0.1.1_all.deb

To upgrade from a downloaded package, run the same command with the new file:

    sudo apt install ./codex-usage-tray_0.1.1_all.deb

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
