**🌐 Sprache / Language:** [English](README.md) · [Deutsch](README.de.md)

# ncpin

Set the Nextcloud virtual-files pin state from the command line — **bypassing the broken Finder menu.**

> **ℹ️ Client v34 changed everything under the hood — ncpin 1.2 adapts automatically.**
> Client v34.0.0 (released 2026-07-28) removed the local socket API on macOS
> ([PR #9463](https://github.com/nextcloud/desktop/pull/9463)): Finder integration now runs over
> XPC, and the client only accepts connections from processes signed with Nextcloud's own team ID —
> ncpin's original transport was gone, with no configuration switch to bring it back.
> Since **ncpin 1.2** the tool therefore uses a second transport on v34+: the sync engine itself
> accepts pin requests as **file renames** in suffix mode
> ([discovery.cpp](https://github.com/nextcloud/desktop/blob/stable-34.0/src/libsync/discovery.cpp):
> *"A suffix vfs file can be downloaded by renaming it to remove the suffix"* — and the reverse
> rename requests dehydration). Clients up to v33 keep using the classic socket; `ncpin doctor`
> shows which transport is active.

For files and folders inside your Nextcloud sync folder, ncpin sets the pin state:

- **keep local** (hydrate / "Always keep on this device") — downloads the real file
- **free up space** (dehydrate / "Online only") — replaces it with a tiny placeholder

## Why

The Nextcloud client normally offers these actions in the Finder right-click menu through its
**FinderSync extension**. On some macOS versions (observed: macOS 26.x with Nextcloud 33.0.5) that
extension can't be kept enabled — and then the menu is gone entirely.

ncpin works around it: the extension is just a messenger. Depending on the client version, ncpin
uses one of two transports (chosen automatically):

- **Socket transport (client up to v33):** the running client offers a local Unix socket; ncpin
  sends the exact same commands (`MAKE_AVAILABLE_LOCALLY` / `MAKE_ONLINE_ONLY`) the extension
  would send.
- **Rename transport (client v34+):** v34 removed that socket. But the sync engine still reads pin
  requests from the filesystem in suffix mode: renaming `file.pdf.nextcloud` → `file.pdf` makes the
  client download the file; the reverse rename makes it dehydrate. ncpin performs exactly these
  renames, reads the state straight from the filesystem, takes the sync roots from the client's
  `nextcloud.cfg`, and verifies every rename against the client's sync journal. Hydration requires
  a virtual record with matching inode and mtime; dehydration requires matching size and mtime.
  A mismatch is rejected before any file in a folder is renamed.

As long as the Nextcloud client is running, ncpin works — extension or not.

## Installation

```sh
git clone <repo> ~/git/ncpin     # or wherever you keep it
cd ~/git/ncpin
./install.sh
```

The installer is idempotent and per Mac:

1. links the `ncpin` CLI into a PATH directory,
2. builds two apps into `/Applications` (`Lokal halten` = keep local, `Speicher freigeben` = free up space) — that target requires a notarized build, see below,
3. installs two Finder **Quick Actions** (right-click → Quick Actions).

Apps and workflows are fully built and verified in a temporary directory before they are installed
with an atomic replacement. Existing unrelated apps, workflows, or CLI targets with the same name
are never overwritten by default. Use `./install.sh --force` for an intentional takeover.
Since ncpin 1.3.6, all five previous artifacts are retained until installation and app registration
finish. A later failure restores them in reverse order; a failed first installation removes the
new artifacts. If another process replaces a target during rollback, the installer preserves the
backup and reports its path instead of overwriting the changed target.
`./install.sh --uninstall` removes only artifacts with the matching ncpin ownership marker or the
exact ncpin symlink.

Version 1.3.9 also handles Unicode case variants that require normalization after case folding. If an installation is interrupted after an atomic swap, the installer detects the completed swap even when the helper is terminated and restores the previous installation, including foreign targets replaced with `--force`.

Version 1.3.8 keeps case-variant requests tied to the requested hardlink, bounds folder sampling by all directory entries, and rolls back interrupted installation swaps.

Version 1.3.7 corrects German app and Quick Action messages, including umlauts in the folder picker.

### Signing, notarization & permissions

If a **Developer ID** certificate is present in your keychain, `install.sh` automatically signs the
two apps (Developer ID + hardened runtime), **notarizes and staples** them, and then checks them with
Gatekeeper. Any signing, notary, staple, or Gatekeeper failure aborts installation and preserves the
previously installed version. Without a certificate it falls back to local ad-hoc signing — such a
build works locally (on first launch you may need to right-click → Open once), but it is **never**
installed into `/Applications`: that folder holds bundles with a stapled notarization ticket only.
The installer stops with exit code 2 before it builds anything, and the same applies to
`NCPIN_NOTARIZE=0`. A plain build (`./build.sh <dir>` or `./install.sh --stage-only <dir>`) also
refuses `/Applications` with exit code 2 — that path checks neither ticket nor collision.
Choose one of these instead:

```sh
./build.sh                            # apps stay in the project folder build/
NCPIN_APPS_DIR=<dir> ./install.sh     # explicitly chosen target, e.g. ~/Applications
```

There are two more entry points, neither of which installs anything:

```sh
./build.sh [target-dir]           # build only → build/ (default)
./release.sh                      # notarized DMG holding the two droplets
./release.sh --no-finder-layout   # skip the Finder window layout (headless)
```

> The DMG carries the two apps only. The Finder right-click Quick Actions and the CLI symlink come from `./install.sh` alone — a disk image cannot populate `~/Library/Services`. A README inside the image says so too.

Each app carries its own copy of the `ncpin` CLI inside the bundle
(`Contents/Resources/ncpin`) and resolves it through a bundle-relative path at runtime. No absolute
path of the build machine ends up in a droplet, so the apps keep working after being dragged out of
the DMG onto another Mac or moved somewhere else. A rebuilt CLI reaches an installed app only
through another `./install.sh` run.

- Auto-detected; controlled via `NCPIN_SIGN_ID`, `NOTARY_PROFILE` or the older `NCPIN_NOTARY_PROFILE` (an
  `xcrun notarytool store-credentials` profile), and `NCPIN_NOTARIZE=0` (intentionally sign only,
  e.g. offline).
  An explicitly empty `NCPIN_SIGN_ID` forces local ad-hoc mode without a keychain lookup.
- The toolbar / Quick-Action paths send Apple Events to Finder. On first use macOS asks
  *"… wants to control Finder"* → **Allow** (System Settings → Privacy & Security → Automation).
  The apps carry the `com.apple.security.automation.apple-events` entitlement so the hardened
  runtime permits this; without it macOS blocks the event before even prompting (error `-1743`).

## Command-line use (also for AI agents / scripts)

**This is the primary, headless-friendly path.** Machine-readable output via `--json`, meaningful
exit codes — drivable straight from scripts and agents.

```sh
ncpin local  ~/Nextcloud/Movie.mp4      # fetch locally (hydrate)
ncpin online ~/Nextcloud/Movie.mp4      # free up space (dehydrate)
ncpin toggle ~/Nextcloud/Folder         # toggle (a folder is recursive)
ncpin status ~/Nextcloud/Movie.mp4      # show current state
ncpin doctor                            # check client / transport / sync folders

ncpin local --wait ~/Nextcloud/Movie.mp4 # block until the state is reached
ncpin status --json ~/Nextcloud/*.pdf    # JSON per path
```

`local`, `online`, and `toggle` require one or more paths and accept `--wait`/`--timeout`; `status`
requires one or more paths, `list` exactly one, and `doctor` none. Invalid arity or options return
exit 2 before any transport is touched. Options may appear between multiple paths, including when
running with the macOS system Python.

The file suffix is resolved automatically: it doesn't matter whether you pass `Movie.mp4` or the
placeholder `Movie.mp4.nextcloud`.

### Exit codes

| Code | Meaning |
|------|---------|
| 0 | ok |
| 1 | runtime error (client not running / no transport / not synced yet / `--wait` timeout) |
| 2 | usage error (bad arguments) |
| 3 | one or more paths not found, or not inside a Nextcloud folder |

### JSON example

```sh
$ ncpin status --json ~/Nextcloud/Examples/Nextcloud\ intro.mp4
[
  {
    "path": "/Users/.../Nextcloud/Examples/Nextcloud intro.mp4.nextcloud",
    "op": "status",
    "state": "online"
  }
]
```

`state` is `local`, `online`, or `unknown`.

### Configuration via environment variables

- `NCPIN_SUFFIX` — placeholder suffix (default `.nextcloud`; ownCloud: `.owncloud`)
- `NCPIN_SOCKET` — strictly force a socket path. A stale override is an error and never falls back
  to a different socket **or to the rename transport**. Automatic discovery accepts only Unix
  sockets that answer with at least one `REGISTER_PATH`.
- `NCPIN_CONFIG` — strictly force the client-config path (`nextcloud.cfg`) the rename transport
  reads its sync roots from (mainly for tests).

## Use in Finder

**Most robust (always works, even with a broken Finder subsystem): the droplet.**

- Put the two apps from `/Applications` in the **Dock**. Select file(s) in Finder and **drag** them
  onto the Dock icon → hydrate / free up space. Needs neither Finder extensions nor AppleEvents.
- Alternatively, drag files directly onto the app (in the Dock, in Finder, or with the app set as a
  drop target in the Finder toolbar).

**When the Finder subsystem is healthy, additionally:**

- **Right-click → Quick Actions → "Lokal halten (Nextcloud)" / "Speicher freigeben (Nextcloud)"**.
- **Toolbar click:** ⌘-drag the app into the Finder toolbar, then click it inside a Nextcloud folder.
  If something is selected, it acts on the selection; otherwise a **picker** lists the current
  folder's contents (multi-select, OK/Cancel, Esc cancels) — no drag, so no risk of accidentally
  moving a folder by missing the icon. (Requires working Finder AppleEvents.)

> **Known limitation (macOS 26.x on some Macs):** the Finder extension/services system can be
> broken — then the Quick Actions don't appear, and Finder AppleEvents time out (`-1712`), so the
> toolbar **click** doesn't work (the app then aborts after 5 s with a specific notice instead of
> hanging). A denied Automation permission (`-1743`) is reported separately, and only a genuinely
> empty selection opens the folder picker. Both error paths point to the **droplet** and **CLI**
> fallbacks; selected paths are not written to a persistent debug log. Symptoms of the same defect:
> the FinderSync extension can't be enabled, and third-party Quick Actions (e.g. PeaZip) disappear
> intermittently.
> A **restart** can revive the subsystem; a plain `killall Finder` often isn't enough.

If the Quick Actions don't show up (on a healthy Finder): System Settings → Login Items &
Extensions → Extensions → Finder → check the boxes, then `killall Finder`.

## How it works

### Socket transport (client up to v33)

The client listens on a Unix socket in its App Group container:

```
~/Library/Group Containers/<TeamID>.com.nextcloud.desktopclient/s
```

Protocol (line-based, UTF-8, each line terminated with `\n`):

```
REGISTER_PATH:/Users/.../Nextcloud         # sent by the server on connect
GET_MENU_ITEMS:<path>                       # returns, among other things, the current pin state
MAKE_AVAILABLE_LOCALLY:<path>               # hydrate / pin "always local"
MAKE_ONLINE_ONLY:<path>                     # dehydrate / free up space
```

ncpin reads the current state in a localization-independent way: from which menu action is
currently active (not greyed out), not from the label text.

### Rename transport (client v34+)

Client v34 removed the socket API on macOS; its XPC replacement only accepts Nextcloud's own
signed processes. ncpin therefore drives the sync engine through the mechanism it has built in
for suffix mode ([discovery.cpp](https://github.com/nextcloud/desktop/blob/stable-34.0/src/libsync/discovery.cpp)):

- **hydrate:** rename `file.pdf.nextcloud` → `file.pdf`. Before the rename, ncpin requires a
  virtual journal record whose inode and mtime match the 1-byte placeholder. A stale inode would
  make the client create a conflict copy instead of recognizing the download request, so ncpin
  rejects it;
- **dehydrate:** rename `file.pdf` → `file.pdf.nextcloud` — the client replaces the content with
  a 1-byte placeholder. The engine only accepts this if size and mtime still match its sync
  journal, so ncpin verifies that **first** (against an APFS clone of the journal, since the
  running client keeps the SQLite locked) and refuses with a clear error instead of leaving a
  silently ignored rename behind;
- **state:** read directly from the filesystem — a placeholder is `<name>.nextcloud` with exactly
  1 byte (the same heuristic the engine itself uses);
- **sync roots:** taken from the client's `nextcloud.cfg` (folders with `virtualFilesMode=suffix`);
  the same path-boundary checks apply as with the socket transport;
- **folders:** recursed by ncpin itself. Both hydration and dehydration preflight the complete
  tree before the first rename, so one stale journal entry or one not-yet-synced file cannot leave
  an intentionally half-converted folder.

Edge case inherited from the engine: a genuine 1-byte file is indistinguishable from a fresh
placeholder and reports as `online`.

## Tests / latency benchmark

`tests/latency_bench.py` measures the per-call **overhead** (connect + handshake + one state
query, no network) and acts as a regression gate, so future changes can't silently make the
drop→done latency creep back up. It runs against the same interpreter the droplet apps use
(macOS system python `/usr/bin/python3`), not your login-shell python.

```bash
python3 tests/latency_bench.py            # measure overhead, check the gate (exit 0/1/2)
python3 tests/latency_bench.py --json      # machine-readable (for CI / agents)
python3 tests/latency_bench.py --roundtrip --sample /path/to/disposable-fixture
```

`--roundtrip` mutates state and therefore always requires an explicitly selected, disposable file
fixture inside a registered sync root. The benchmark reads the initial state, validates every
individual run, and restores the verified state in `finally` even after an error. It never performs
a blind counter-operation when the state cannot be determined safely.

Run the deterministic fake-socket, rename-transport, parser, benchmark, and isolated installer
suite with:

```bash
PYTHONDONTWRITEBYTECODE=1 /usr/bin/python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Exit codes: `0` gate green · `1` overhead over threshold (default 1.0 s) or wrong state ·
`2` precondition missing (no client/transport — `ncpin doctor` must be green first).

## Requirements

- macOS, a running Nextcloud desktop client with a sync folder in **virtual-files mode**
  (`suffix`). Clients up to v33 are driven over the socket API, v34+ over the rename transport.
- python3 ≥ 3.7 — no external dependencies. The system python that ships with macOS
  (`/usr/bin/python3`) is enough; the droplet apps invoke exactly that.
- Safety guard: ncpin only touches real, symlink-resolved paths **inside** a registered Nextcloud
  folder and rechecks that boundary immediately before filesystem and transport operations.

## Multi-Mac

There are no hard-coded user or repository paths: the apps safely embed the installed CLI's actual
path. Run `./install.sh` once per Mac; the active transport is detected dynamically.

## License

MIT — see [LICENSE](LICENSE).
