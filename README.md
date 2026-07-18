# Codex Usage Tray

Unofficial Ubuntu GNOME tray indicator for OpenAI Codex usage limits.

Codex Usage Tray reads rate-limit information from the local Codex app-server
and displays the remaining allowance in the top panel. It also checks Codex
account state before requesting usage limits, so new users see a clear sign-in
message instead of a generic unavailable state.

## Requirements

- Ubuntu GNOME with AppIndicator support
- Python 3.11 or newer
- OpenAI Codex installed and authenticated separately

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

When Codex is signed in, the menu can show the account email address and plan
reported by Codex. After successful authentication, Codex Usage Tray reads the
account again and refreshes usage limits immediately, without requiring a
restart.

## Installation

Download the latest `.deb` package from GitHub Releases and install it with:

    sudo apt install ./codex-usage-tray_0.1.1_all.deb

To upgrade from a downloaded package, run the same command with the new file:

    sudo apt install ./codex-usage-tray_0.1.1_all.deb

Start the application immediately with:

    codex-usage-tray

The application is also configured to start automatically after the next login.
The package installs a system-wide GNOME autostart entry at
`/etc/xdg/autostart/codex-usage-tray.desktop`; each desktop user still uses
their own Codex authentication.

## Tray menu

- **Sign in to Codex…** is available when Codex reports that no account is
  signed in. It starts the Codex-managed browser authentication flow.
- **Refresh** checks account state and usage limits immediately. The application
  also refreshes automatically every five minutes.
- **Quit** stops the current tray process. The application starts again at the
  next desktop login while the autostart entry remains enabled.

## Data source and privacy

The application communicates only with a local `codex app-server` subprocess.
It requests the current account state and rate limits through that supported
local protocol; it does not scrape ChatGPT pages.

Codex Usage Tray does not directly read authentication tokens, browser cookies,
browser data, or Codex authentication files such as `~/.codex/auth.json`.
Authentication and any network communication required for it remain under
Codex's control. The account email and plan returned by Codex are held only in
memory and shown locally in the tray menu; this application does not store or
log them.

## Diagnostics

If the tray shows `Usage unavailable.`, first verify that Codex is available to
the desktop session and complete authentication if needed:

    codex --version
    codex login

You can then restart the tray from the desktop launcher or run:

    codex-usage-tray

These checks do not require opening or copying token, cookie, browser, or Codex
authentication files. Bug reports should contain the package version, Ubuntu
version, desktop environment, and the displayed status message, but no
credentials or authentication-file contents.

Codex Usage Tray is an unofficial community application and is not affiliated
with or endorsed by OpenAI.

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
