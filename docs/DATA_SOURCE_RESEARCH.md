# Codex Usage Tray Data Source Research

Research date: 2026-07-17  
Target environment: Ubuntu GNOME on Wayland

## Executive summary

A documented local mechanism exists for reading Codex rate limits. Codex CLI 0.121.0 exposes `account/rateLimits/read` through the app-server JSONL protocol. A sanitized live request succeeded without directly reading browser data, cookies, tokens, credential files, account identifiers, or session contents.

The recommended MVP should use a long-lived `codex app-server` child process over standard input and output. It should request current limits through `account/rateLimits/read`, accept `account/rateLimits/updated` notifications, and perform a slow periodic refresh. Codex should continue to manage its own authentication internally.

Parsing local Codex session files should not be the default fallback. Those files contain historical point-in-time snapshots mixed with private session content, and sampled records did not match the live account result. For the MVP, failure of the supported interface should produce a clear "Usage unavailable" state instead.

## Repository state during research

- Repository: `codex-usage-tray`
- Branch: `main`, aligned with `origin/main`
- Commit: `b343701 chore: initialize repository`
- Tracked project content before this report: `README.md` only
- No repository-specific `AGENTS.md` was present.
- No source code was implemented.
- No commit or push was performed.

The existing README contained:

```text
# Codex Usage Tray

Unofficial Ubuntu tray indicator for OpenAI Codex and ChatGPT Work usage limits.
```

## Local environment

Sanitized environment output:

```text
Ubuntu 24.04.4 LTS
Linux 6.17.0-35-generic x86_64 GNU/Linux
Python 3.12.3
XDG_SESSION_TYPE=wayland
XDG_CURRENT_DESKTOP=ubuntu:GNOME
WAYLAND_DISPLAY=wayland-0
GNOME Shell 46.0
```

Installed UI packages and bindings:

```text
python3                                      3.12.3-0ubuntu2.1
python3-gi                                   3.48.2-1
gir1.2-gtk-3.0:amd64                        3.24.41-4ubuntu1.3
gir1.2-ayatanaappindicator3-0.1              0.5.93-1build3
libayatana-appindicator3-1                   0.5.93-1build3
gnome-shell-extension-appindicator           58-1ubuntu24.04.1
GI_IMPORT                                    ok
GTK_VERSION                                  3.24.41
AYATANA_NAMESPACE                            ok
```

The `gnome-shell-extension-appindicator` package is installed and its metadata supports GNOME Shell 45 and 46. It was not listed among the enabled user extensions during the command-line check. The enabled state therefore needs a visual runtime check before relying on panel label behavior.

## Installed Codex version and launcher condition

The Codex launcher resolves as follows:

```text
/usr/local/bin/codex
/usr/local/lib/node_modules/@openai/codex/bin/codex.js
```

The installed npm package metadata reports:

```text
package: @openai/codex
version: 0.121.0
required Node.js engine: >=16
```

The public launcher is currently broken in this shell because Node.js is not installed:

```text
/usr/bin/env: ‘node’: No such file or directory
```

The npm package includes a native Linux binary. It was used directly for read-only research and reported:

```text
codex-cli 0.121.0
```

Production project code should not hard-code the npm vendor path. It should locate `codex` through `PATH`, validate that it can run, and display a useful diagnostic if the launcher is broken. In this particular environment, the external prerequisite can be repaired by installing a compatible Node.js runtime or reinstalling Codex through another supported distribution method. That environment repair was not performed during this research.

## Available Codex commands

The installed top-level help exposes:

```text
exec
review
login
logout
mcp
marketplace
mcp-server
app-server
completion
sandbox
debug
apply
resume
fork
cloud
exec-server
features
help
```

Relevant command surfaces:

- `codex app-server`: runs the local app server. The installed build supports JSONL over stdio by default, WebSocket via `ws://IP:PORT`, or no listener. The CLI labels the command experimental.
- `codex mcp-server`: starts Codex as an MCP server over stdio. It is intended mainly for another agent consuming Codex.
- `codex debug app-server send-message-v2`: a debugging surface for app-server flows, not a dedicated rate-limit command.
- No `codex status`, `codex usage`, or other one-shot machine-readable rate-limit command appears in this installed command set.
- The installed 0.121.0 app-server help does not expose Unix-socket daemon discovery. WebSocket transport is experimental and unnecessary for the MVP.

## Supported rate-limit interface

Read-only inspection of the installed native binary found the following protocol names and fields:

```text
account/rateLimits/read
account/rateLimits/updated
GetAccountRateLimitsResponse
RateLimitSnapshot
rateLimits
rateLimitsByLimitId
usedPercent
windowDurationMins
resetsAt
primary
secondary
```

Official OpenAI source documentation for the installed `rust-v0.121.0` tag confirms that:

- `codex app-server` is the local interface used to power rich Codex clients such as the VS Code extension.
- The stdio protocol is newline-delimited JSON with JSON-RPC 2.0 framing but without the `jsonrpc` header on the wire.
- A client must send `initialize`, wait for its response, then send the `initialized` notification before making other requests.
- `account/rateLimits/read` fetches ChatGPT rate limits.
- Updates may arrive through `account/rateLimits/updated`.
- `usedPercent` is the percentage already used in the quota window.
- `windowDurationMins` is the duration of the quota window in minutes.
- `resetsAt` is a Unix timestamp in seconds for the next reset.
- The rate-limit method is available without opting into `experimentalApi`, even though the enclosing app-server command itself remains experimental.

References:

- [OpenAI Codex app-server documentation for version 0.121.0](https://github.com/openai/codex/blob/rust-v0.121.0/codex-rs/app-server/README.md)
- [Current OpenAI Codex CLI command reference](https://developers.openai.com/codex/cli/reference/#codex-app-server)

## Live protocol verification

The app-server process was started with analytics explicitly disabled:

```text
codex -c analytics.enabled=false app-server
```

For this research, the bundled native executable was substituted for `codex` because the public npm launcher could not start without Node.js.

The sequential protocol messages were equivalent to:

```json
{"method":"initialize","id":30,"params":{"clientInfo":{"name":"codex_usage_tray_research","title":"Codex Usage Tray Research","version":"0.0.0"}}}
{"method":"initialized","params":{}}
{"method":"account/read","id":31,"params":{"refreshToken":false}}
{"method":"account/rateLimits/read","id":32}
```

The first attempt sent messages too quickly and received the expected protocol error:

```json
{"code":-32600,"message":"Not initialized"}
```

Waiting for the initialization response before sending subsequent messages resolved the issue.

The first properly sequenced rate-limit request inside the restricted network sandbox reached the local app-server but could not reach the upstream service:

```json
{
  "errorCode": -32603,
  "errorMessage": "failed to fetch codex rate limits: error sending request for url (https://chatgpt.com/backend-api/wham/usage)"
}
```

Repeating the same sanitized, read-only request with network permission succeeded. Only whitelisted non-identifying fields were printed:

```json
{
  "initialized": true,
  "account": {
    "signedIn": true,
    "accountType": "chatgpt",
    "planType": "plus",
    "requiresOpenaiAuth": true
  },
  "rateLimitRead": {
    "rateLimits": {
      "primary": {
        "usedPercent": 4,
        "windowDurationMins": 10080,
        "resetsAt": 1784889359
      },
      "secondary": null,
      "rateLimitReachedType": null
    },
    "additionalLimitCount": 1
  }
}
```

A final whitelist-only enumeration showed that the apparent additional entry was the same snapshot repeated in `rateLimitsByLimitId`, not a separate quota:

```json
{
  "default": {
    "limitId": "codex",
    "limitName": null,
    "primary": {
      "usedPercent": 4,
      "windowDurationMins": 10080,
      "resetsAt": 1784889359
    },
    "secondary": null,
    "rateLimitReachedType": null
  },
  "byLimitId": [
    {
      "mapKey": "codex",
      "limitId": "codex",
      "limitName": null,
      "primary": {
        "usedPercent": 4,
        "windowDurationMins": 10080,
        "resetsAt": 1784889359
      },
      "secondary": null,
      "rateLimitReachedType": null
    }
  ]
}
```

Interpretation of the live result:

- The value is `4% used`, so the derived remaining amount is `96%`.
- `10080` minutes is seven days.
- `1784889359` is `2026-07-24 10:35:59 UTC` or `2026-07-24 12:35:59 CEST` in Warsaw.
- Only one distinct quota window was present at the time of the request.
- The secondary window was absent.
- `primary` and `secondary` are positions, not reliable display labels such as "five-hour" and "weekly".
- The UI must label windows from `windowDurationMins` and tolerate missing, additional, or reordered windows.
- `rateLimitsByLimitId` must be deduplicated against the default `rateLimits` snapshot.

No email address, account identifier, token, credential, credit balance, or session content was displayed. Codex used its own existing authentication internally; the research did not open or parse its credential store.

## Recommended primary data source

Use one long-lived app-server child process over stdio:

1. Locate a working `codex` executable with `PATH`.
2. Start `codex -c analytics.enabled=false app-server`.
3. Send `initialize` with minimal, non-identifying client metadata.
4. Wait for the matching response.
5. Send the `initialized` notification.
6. Call `account/rateLimits/read`.
7. Continue reading `account/rateLimits/updated` notifications.
8. Perform a slow periodic read, recommended initially at five-minute intervals, because notifications alone may not keep an idle display fresh.
9. Offer manual refresh.
10. Use exponential backoff with jitter after startup, transport, authentication, or backend failures.
11. Prefer the `rateLimitsByLimitId` map when it is present, but deduplicate entries that repeat the default snapshot.
12. Derive remaining usage as `clamp(100 - usedPercent, 0, 100)`.
13. Format each window from `windowDurationMins` rather than assuming fixed five-hour and weekly slots.
14. Convert `resetsAt` from Unix seconds to the user's local time only at presentation time.

The process should remain alive rather than being spawned for every refresh. This reduces startup overhead and permits receipt of update notifications.

The application should never log raw app-server messages. It should parse each response into a narrow internal model, retain only required quota fields, and discard the raw payload. It should not persist account data or quota responses to disk.

## Recommended fallback behavior

For the MVP, the fallback should be a clear unavailable state, not undocumented file parsing. Suggested states include:

- Codex CLI not found
- Codex launcher is not executable
- Installed Codex version does not expose the method
- Codex is not signed in with ChatGPT
- App-server initialization failed
- Network unavailable
- Rate-limit service unavailable
- Response unsupported or incomplete

The menu can show a concise diagnostic and a manual retry action without exposing raw errors that might contain private data.

If a session-file fallback is considered later, it should be explicitly opt-in, disabled by default, restricted to rate-limit event fields, and visibly marked as historical or potentially stale. It should never access `auth.json`, browser storage, cookies, logs, or prompt history.

## Local session-file fallback assessment

A privacy-preserving structural scan was restricted to Codex session directories and returned:

```text
sessions files_with_rate_limits=33
archived_sessions directory_absent
```

Candidate files follow a pattern similar to:

```text
~/.codex/sessions/**/rollout-*.jsonl
```

Rate-limit snapshots occur in `event_msg` records with a `token_count` payload and snake_case fields. A sanitized sample was:

```json
{
  "limit_id": "codex",
  "primary": {
    "used_percent": 100.0,
    "window_minutes": 10080,
    "resets_at": 1784667371
  },
  "secondary": null,
  "plan_type": "plus"
}
```

The sampled rollout record reported `100% used`, while the live request reported `4% used`. This confirms that session rollouts contain point-in-time state and cannot be treated as authoritative current account data.

Reliability and stability limitations:

- Snapshots are produced as part of session activity rather than as an independent current-account feed.
- Idle sessions may not receive new snapshots.
- Resumed, replayed, or forked sessions can surface historical data.
- Multiple files can contain conflicting snapshots.
- Selecting the correct snapshot across sessions, accounts, and capture times is error-prone.
- The rollout schema is not documented as a stable external-consumer API.
- Fields and nesting may change between Codex versions.
- Snapshot timestamps and outer event timestamps have different meanings.

Privacy limitations:

- Rollout files contain complete conversation and tool activity, not only rate limits.
- They may include prompts, responses, commands, paths, repository information, images, and session identifiers.
- A careless parser, debug log, exception, or bug could expose unrelated private data.
- Reading `auth.json` would directly violate the project's credential boundary and is not acceptable.
- Browser cookies and ChatGPT web storage must never be used.

## Smallest reliable MVP architecture

Proposed module structure:

```text
src/codex_usage_tray/
├── __main__.py
├── app.py
├── codex_client.py
├── models.py
└── formatting.py

tests/
├── test_codex_client.py
├── test_models.py
└── test_formatting.py
```

Responsibilities:

- `__main__.py`: process entry point and top-level error handling.
- `app.py`: GTK main loop, Ayatana AppIndicator construction, menu state, refresh scheduling, and clean shutdown.
- `codex_client.py`: Codex executable discovery, version/capability checks, long-lived app-server subprocess, sequential JSONL handshake, request correlation, notification parsing, retry logic, and process shutdown.
- `models.py`: narrow validated representations of limit sets and windows; remaining-percentage calculation; deduplication; null and unknown-field handling.
- `formatting.py`: duration labels, remaining percentages, reset-time formatting, and English user-facing status strings.
- `tests/`: protocol tests with a fake subprocess plus pure unit tests for percentage, duration, reset-time, missing-window, multiple-limit, malformed-response, and deduplication behavior.

Main-loop design:

- One worker thread owns all blocking child-process reads and writes.
- The worker sends parsed state back through a thread-safe queue or `GLib.idle_add()`.
- `GLib.timeout_add()` schedules refresh requests but never performs subprocess or network I/O itself.
- GTK and AppIndicator mutations occur only on the GTK main thread.
- Shutdown cancels scheduled callbacks, closes stdin, terminates the child gracefully, and then applies a bounded forced termination only if necessary.
- Raw JSON and stderr are not persisted. Error text is reduced to safe internal categories before reaching the UI.

Suggested initial UI:

- A generic symbolic system icon, not an OpenAI or ChatGPT logo.
- A short panel label showing the lowest remaining percentage across active windows, subject to product review.
- Menu rows for each distinct window, for example `7-day limit: 96% remaining`.
- Reset time for each available window.
- Last successful refresh time.
- Safe unavailable or stale state.
- `Refresh` and `Quit` actions.

No logo asset should be added until its redistribution license and applicable OpenAI brand-use rules have been reviewed. A generic theme icon avoids blocking the MVP on brand approval.

## Ubuntu dependencies

Direct runtime packages:

```bash
sudo apt install \
  python3 \
  python3-gi \
  gir1.2-gtk-3.0 \
  gir1.2-ayatanaappindicator3-0.1 \
  gnome-shell-extension-appindicator
```

Important transitive runtime libraries include:

```text
libayatana-appindicator3-1
GTK 3 runtime libraries
GLib and GObject introspection runtime libraries
```

External prerequisites:

- A working Codex CLI installation with app-server support.
- A ChatGPT-managed Codex login.
- Node.js 16 or later when Codex is installed through the npm launcher examined here.
- The Ubuntu AppIndicators GNOME Shell extension installed and enabled.

The application should not require an OpenAI API key. It should not implement its own OAuth flow, token refresh, credential store, or browser integration.

## Risks

### Technical risks

- The app-server command is experimental and may change between Codex releases.
- JSONL initialization must be sequential; requests sent before initialization are rejected.
- The app-server child can crash, hang, or emit malformed or unexpected messages.
- Authentication can expire or change while the indicator is running.
- Network and backend failures must not freeze GTK.
- Polling too frequently would create unnecessary authenticated backend traffic.
- Process shutdown and restart logic can leak children if implemented carelessly.
- The current public `codex` launcher in this environment cannot run until Node.js is restored.

### Data-model risks

- `usedPercent` means used, while the product intends to display remaining.
- Windows can be absent, added, reordered, or repeated.
- `primary` is not a reliable semantic name.
- A five-hour window cannot be assumed to exist.
- `rateLimitsByLimitId` can repeat the default snapshot.
- Reset timestamps are seconds, not milliseconds.
- Percentages and timestamps may be temporarily inconsistent during backend transitions.

### Privacy risks

- Raw account or app-server responses may contain fields the UI does not need.
- Session files contain unrelated private user data.
- Debug logging could accidentally disclose raw JSON, paths, email addresses, identifiers, or credentials.
- Reading `auth.json`, browser cookies, browser profiles, or web storage would violate the project's boundary.
- Client metadata is part of app-server initialization; enterprise deployments may need the client name recognized for OpenAI compliance logging.

### Compatibility risks

- App-server fields and transports may differ by Codex version.
- Older Codex builds may lack the method or response fields.
- GNOME Shell requires an AppIndicator extension for legacy indicator integration.
- Indicator labels may be clipped or hidden depending on GNOME extension behavior, theme, scaling, and panel layout.
- Wayland prevents assumptions based on X11 tray behavior.
- Python GI namespace availability varies across distributions, so the initial project should target Ubuntu explicitly.

### Maintenance and public-project risks

- A version compatibility policy and fixtures for multiple Codex response shapes will be needed.
- Official protocol changes must be monitored.
- Public terminology involving OpenAI, Codex, ChatGPT, and "ChatGPT Work" should be reviewed for accuracy and trademark presentation.
- No OpenAI or ChatGPT logo should be bundled until redistribution and brand-use conditions are verified.
- The project should clearly identify itself as unofficial and avoid implying endorsement.

## Open questions

1. What exactly does "ChatGPT Work" refer to in the public project wording? The verified payload exposed a `codex` quota under a ChatGPT Plus account, not a separately named Work quota.
2. Should the panel show the lowest remaining percentage, the shortest window, a selected window, or rotate between windows?
3. Should Codex 0.121.0 be the minimum supported version, or should the application rely only on capability detection?
4. Is a five-minute background refresh interval acceptable?
5. Should session-file parsing remain entirely excluded, or be considered later as an explicit opt-in stale-data fallback?
6. Should the first release run manually, or include GNOME autostart packaging?
7. Should a broken Codex launcher be treated only as a diagnostic, or should installation guidance be part of the application menu?
8. Is displaying plan type desirable, or should the UI omit all account-level metadata?
9. Does the proposed client identifier need registration or review for enterprise compliance-log environments?
10. Does the installed AppIndicators extension render text labels reliably in the target GNOME configuration?

## Recommended next implementation step

After this approach is reviewed and explicitly approved:

1. Repair or replace the broken external Codex launcher so `codex --version` works normally.
2. Add the minimal Python package scaffold and tests.
3. Implement `models.py` and `formatting.py` first using sanitized fixtures.
4. Implement a narrow `codex_client.py` that performs only initialize, initialized, `account/rateLimits/read`, and `account/rateLimits/updated` handling with analytics disabled.
5. Test the client against a fake JSONL process before any live integration.
6. Add the GTK/AppIndicator shell last, ensuring every blocking operation remains off the GTK main thread.
7. Use a generic symbolic icon and defer brand assets.
8. Keep session-file fallback out of the first implementation.

## Commands run

The research used the following read-only command groups. Long inline protocol scripts are described by purpose because they contained only the JSON messages and whitelist filters shown above.

### Repository inspection

```bash
pwd
rg --files -g 'AGENTS.md' -g '!**/.git/**' . ..
git status --short --branch
find . -maxdepth 2 -type f -printf '%P\n' | sort
sed -n '1,220p' README.md
git log -1 --oneline --decorate
git diff --stat
git status --porcelain=v1
```

### Codex installation inspection

```bash
command -v codex
readlink -f "$(command -v codex)"
codex --version
codex --help
codex app-server --help
codex debug --help
sed -n '1,240p' /usr/local/lib/node_modules/@openai/codex/bin/codex.js
sed -n '1,220p' /usr/local/lib/node_modules/@openai/codex/package.json
find /usr/local/lib/node_modules/@openai -maxdepth 8 -type f -print
<bundled-native-codex> --version
<bundled-native-codex> --help
<bundled-native-codex> app-server --help
<bundled-native-codex> debug app-server --help
<bundled-native-codex> mcp-server --help
<bundled-native-codex> features --help
strings <bundled-native-codex> | rg -i 'account/rate.?limits/read|rate.?limits|used_percent|resets_at'
type -a node
type -a nodejs
nodejs --version
dpkg-query -s nodejs
```

### Sanitized protocol verification

```text
python3 -c '<in-memory sequential JSONL driver that started app-server, sent initialize/initialized/account requests, whitelisted quota fields, terminated the child, and wrote no files>'
```

The child command was equivalent to:

```bash
codex -c analytics.enabled=false app-server
```

Initial pipe-based probes were also attempted with `timeout`, `printf`, and `jq`. They established that closing stdin immediately can terminate the server before a response and that sending account requests before the initialize response produces `Not initialized`.

### Environment and dependency inspection

```bash
lsb_release -ds
uname -srmo
python3 --version
gnome-shell --version
gsettings get org.gnome.shell enabled-extensions
gsettings get org.gnome.shell disabled-extensions
gsettings get org.gnome.shell disable-user-extensions
dpkg-query -W python3 python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 libayatana-appindicator3-1 gnome-shell-extension-appindicator
python3 -c "import gi; gi.require_version('Gtk','3.0'); gi.require_version('AyatanaAppIndicator3','0.1'); from gi.repository import Gtk, AyatanaAppIndicator3; print('GI_IMPORT=ok')"
gnome-extensions list --enabled
gnome-extensions info ubuntu-appindicators@ubuntu.com
dpkg -L gnome-shell-extension-appindicator
```

### Session fallback inspection

```bash
rg -l '"rate_limits"\s*:' /home/elluce/.codex/sessions
rg -l0 '"rate_limits"\s*:' /home/elluce/.codex/sessions /home/elluce/.codex/archived_sessions | xargs -0 jq '<whitelisted rate-limit fields only>'
```

### Timestamp conversion

```bash
date -u -d @1784889359 '+live reset UTC: %Y-%m-%d %H:%M:%S %Z'
TZ=Europe/Warsaw date -d @1784889359 '+live reset Warsaw: %Y-%m-%d %H:%M:%S %Z'
date -u -d @1784667371 '+fallback sample reset UTC: %Y-%m-%d %H:%M:%S %Z'
```

## Research boundary confirmation

During the research phase:

- No application source code was created.
- No browser pages were scraped for ChatGPT account data.
- No browser cookies, passwords, OAuth tokens, API keys, or credential files were accessed or stored.
- No account identifiers, email addresses, or complete session contents were displayed.
- No telemetry was added; the app-server verification explicitly set `analytics.enabled=false`.
- No logo asset was downloaded or added.
- No commit was created.
- Nothing was pushed.

