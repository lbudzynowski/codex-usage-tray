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
- Codex CLI 0.146.0 or a compatible version exposing `remote-control` and
  `app-server proxy`
- `systemd-inhibit` (provided by systemd) for sleep protection, which is
  enabled by default

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

    sudo apt install ./codex-usage-tray_0.2.0_all.deb

To upgrade from a downloaded package, run the same command with the new file:

    sudo apt install ./codex-usage-tray_0.2.0_all.deb

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
- The **Remote Control** section shows stopped, starting, connected,
  disconnected, error, or unavailable state and includes the host name when
  Codex reports one.
- **Start Remote Control** and **Stop Remote Control** call the official Codex
  CLI daemon commands. The actions are enabled only when appropriate.
- **Pair a device…** requests an official short-lived manual pairing code and
  keeps it only in memory. The dialog supports copying, expiry countdown,
  successful-claim detection, and generating a replacement code.
- **Prevent sleep while Remote Control is active** is enabled by default and
  blocks automatic suspend and logind idle sleep only while Remote Control is
  connected. It can be disabled for the current tray session.
- **Quit** stops the current tray process. The application starts again at the
  next desktop login while the autostart entry remains enabled.

## Codex Remote Control

Remote Control is an experimental Codex feature and its command and JSON
contracts may change between CLI versions. This application was verified with
Codex CLI 0.146.0. Older or incompatible CLIs are shown as **Remote Control:
Unavailable** rather than being treated as stopped.

The tray passively reads the running daemon's state through the official local
`codex app-server proxy` control channel. It does not start Remote Control merely
to discover its status. Starting, stopping, and pairing occur only after the
corresponding user action.

To pair a phone, first wait for **Remote Control: Connected**, choose **Pair a
device…**, then enter the displayed code in the ChatGPT mobile app. Pairing
codes, opaque pairing payloads, and environment identifiers are not logged or
persisted by Codex Usage Tray.

Codex currently returns an opaque `pairingCode` in addition to the documented
manual code, but the public protocol does not define that value as a QR scanner
payload. Codex Usage Tray therefore does not generate a QR code. In particular,
it does not invent a QR format from `manualPairingCode`; QR support can be added
when OpenAI publishes or otherwise confirms the scanner contract.

Sleep protection is enabled by default and runs a local `systemd-inhibit`
process scoped to `sleep:idle`. It is acquired only while Remote Control is
connected and the checkbox is enabled, and is released after disconnection,
stop, disabling the checkbox, or quitting the tray. The preference is not
persisted between tray sessions. The screen may still dim, turn off, or lock.

The tray does not modify GNOME or logind laptop-lid configuration. Lid-close
behavior remains controlled by the operating system, and an inhibitor may be
respected or ignored depending on settings such as `LidSwitchIgnoreInhibited`.
Do not assume that closing the lid will preserve Remote Control. Never place
the laptop in a bag while it remains powered and awake.

Codex Usage Tray does not open or listen on a network port. Remote connectivity
and authentication remain owned by the separately installed Codex daemon; the
tray only uses local CLI and local control-socket interfaces.

## Data source and privacy

The usage-limit feature communicates only with a local `codex app-server`
subprocess. It requests the current account state and rate limits through that
supported local protocol; it does not scrape ChatGPT pages. Remote Control uses
the local Codex CLI and its local daemon control socket as described above.

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

For Remote Control failures, also inspect the installed command surface without
starting or stopping the daemon:

    codex remote-control --help
    codex remote-control start --help
    codex remote-control pair --help
    codex app-server proxy --help

An **Unavailable** state usually means the installed CLI or its managed daemon
does not expose the required experimental methods. **Disconnected** means
Remote Control is enabled but has not established its relay connection; check
normal network and proxy access. **Error** covers timeouts, malformed JSON,
control-socket failures, and non-zero command exits. The tray intentionally
does not include raw pairing output or potentially sensitive daemon output in
diagnostics.

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
