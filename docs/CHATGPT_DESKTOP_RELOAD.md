# ChatGPT Linux desktop reload — reproducible investigation

Tracked under [issue #9](https://github.com/lbudzynowski/codex-usage-tray/issues/9).
Working branch: `feature/chatgpt-desktop-refresh`.

## Scope and confirmed environment

- Ubuntu Linux, ChatGPT Debian package `26.1027.51404` (amd64).
- Installed Electron archive: `/usr/lib/chatgpt/resources/app.asar`.
- Archive size: `670826973` bytes (about 671 MB).
- **Original** first 1 MiB SHA256: `9d9a862d8c6eb31b9092ecd50fa1cd82b729621720deea1b2f342f827556fc82`. This is an identification fingerprint, **not** a full-file integrity checksum.
- Working backup created locally in `/var/backups/chatgpt-asar/app.asar.26.1027.51404.original`.
- The user confirmed that a local two-substitution patch enabled **Reload Window** in the installed ChatGPT app.

Only **window/UI reload** has been confirmed as working. **Conversation sidebar refresh, active-thread preservation, and unsent draft preservation have not been independently validated.**

## What static ASAR analysis found

Read-only scanner: `scripts/chatgpt_asar_recon.py`.

The corrected ASAR scanner found **22,559 indexed entries**, including **16,352 JavaScript entries**. The initial installation-file-only scan failed to decode ASAR and must **not** be used as evidence that reload code is missing.

The original captured report counted:

| Pattern | Occurrences |
| --- | ---: |
| `allowWindowReload` | 2 |
| `Reload Window` | 1 |
| `reloadWindow` | 65 |
| `webContents.reload` | 3 |
| `reloadIgnoringCache` | 3 |
| `window.location.reload` | 42 |
| `CmdOrCtrl+R` | 3 |
| `refreshConversation` | 4 |
| `invalidateQueries` | 769 |
| `refetchQueries` | 27 |
| `thread/list` | 30 |
| `thread/read` | 40 |
| `thread/resume` | 27 |
| `thread/loaded/list` | 3 |
| `ipcMain.handle` | 8 |
| `ipcRenderer.invoke` | 12 |

Key entry within ASAR:

```
.vite/build/early-bootstrap-439-a0f25ba32c465de1.js
```

Important code markers from this release:

```javascript
allowWindowReload:r!==eH.x.Prod
// In the command menu:
label: "Reload Window"
accelerator: "Command+Control+R"
// click invokes the existing window.reload() method.
```

The renderer bundle `webview/assets/app-initial-fdea07eb2719.js` contains
`refreshRecentConversations({mode:"expanded"})`, `invalidateQueries`, `refetchQueries`,
and Codex RPC methods such as `thread/list` and `thread/read`.
**These names do not establish a supported external API**, nor prove that
side-panel and active-thread refresh can be triggered from a separate tray process.
`refreshConversationExecutorPlugins` concerns plugins, not conversation history.

## Reproducible tools in this branch

- `scripts/chatgpt_asar_recon.py` — read-only ASAR inventory and bounded grep.
- `scripts/chatgpt_asar_reload.py` — version-locked, reversible patch + read-only status and restore modes.
- `tests/test_chatgpt_asar_reload.py` — synthetic ASAR/index tests (no proprietary application bytes required).

The patch is deliberately separate from the `codex-usage-tray` runtime and
is **not bundled into the Debian installer, autostart, or routine updates**.
It is an opt-in research tool, not an OpenAI-supported modification.

### Inspect the currently installed build

```bash
python3 scripts/chatgpt_asar_reload.py --status
python3 scripts/chatgpt_asar_recon.py --output ~/Downloads/chatgpt-asar-report.txt
```

The first command reports whether the targeted JavaScript appears original,
patched, or unknown; it makes no changes. The second rechecks ASAR static code.

### Patch an unmodified matching installation only

**Close ChatGPT completely** before editing Electron resources.
Run only on the exact pinned version and fingerprint above:

```bash
sudo python3 scripts/chatgpt_asar_reload.py --apply
```

The tool refuses unknown builds, already-patched source, running ChatGPT, and
existing backup paths. It creates a verified original ASAR backup and applies
two length-preserving substitutions. It also updates ASAR per-entry SHA256
integrity metadata, when present.

* Production reload gate becomes `allowWindowReload:!0`.
* Shortcut becomes `Control+Shift+R`.

The **Haiyan installation was already patched interactively** before these files
were added to GitHub. Do **not** run `--apply` again on that existing patched
installation. Use `--status` instead.

### Roll back

Close ChatGPT fully, then:

```bash
sudo python3 scripts/chatgpt_asar_reload.py --restore
```

Restore requires the **matching original backup**, the expected package
version, and recognized patch markers; it refuses to overwrite an unknown
version or unexpectedly modified ASAR.

Manual fallback on the exact verified original version, after quitting ChatGPT:

```bash
sudo cp -a /var/backups/chatgpt-asar/app.asar.26.1027.51404.original /usr/lib/chatgpt/resources/app.asar
```

**Do not use this backup to overwrite a newer package after APT upgrades.**
Package upgrades can replace `app.asar` and invalidate the patch; re-inspect
each release instead of blindly reapplying bytes from the previous release.

Backup archive, patched ASAR, tokens, profiles, and proprietary bundled Electron
source are intentionally **not committed** to GitHub.

## Next stage: tray integration and return to current chat

1. Confirm whether `Reload Window` preserves the selected conversation after
   the user reloads the actual ChatGPT window (and after switching conversations).
2. Check behavior with a draft in the composer and with an active assistant turn.
   Never assume drafts/ongoing work survive a renderer reload.
3. Determine whether an official/safe desktop action can be triggered by the
   existing `codex-usage-tray` without touching private Codex sockets.
4. If a supported approach exists, add an explicit **Reload ChatGPT UI** tray item,
   graceful detection, and tests. Do not disguise app restart as renderer reload.
5. Investigate separate **Refresh conversations** and **Refresh active chat** only
   if they can be implemented without private IPC injection or unsound cache edits.

Upstream context:
- [openai/codex #33799](https://github.com/openai/codex/issues/33799)
- [openai/codex #51994](https://github.com/openai/codex/issues/51994)
- [openai/codex #21743](https://github.com/openai/codex/issues/21743)

**Status:** evidence and patch archived, pending review/tests. No integration with
tray UI or automatic installation changes yet, and no merge to `main`.
