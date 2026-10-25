# Spec: ES-DE library mode — the whole RomM library in ES-DE, downloaded on demand, saves synced

Fork: `ProphetWild/romm-tender`, based on upstream `0.33.0` (`34104a2`). This file is the epic's body; it follows the
repo's own workflow (`CLAUDE.md`, "Where decisions live"), so open questions sit under `## To decide` and nothing merges
until each is checked off into `## Decisions`.

## Goal

Play the full RomM library from ES-DE inside RetroDECK on a Steam Deck, with no Steam shortcuts:

1. Every RomM game in the enabled platforms appears in ES-DE, with RomM's metadata and artwork, whether downloaded or not.
2. Launching a game that is not downloaded downloads it first, then starts it. No trip to the QAM panel.
3. Saves sync with RomM around every ES-DE launch: pull before start, push after exit.
4. Tender creates no Steam shortcuts in this mode. The QAM panel stays as the settings, download and BIOS surface.

Environment: RomM runs on a Mac mini, reached on the LAN and remotely through Tailscale. The Deck uses one RomM URL (the
Mac mini's MagicDNS name) everywhere.

## What upstream already gives us

Read these before writing code; each is the current owner of its area.

- **ADR-0042** (`docs/adr/0042-…`): Tender starts emulators itself, keeps downloaded games in **Tender's own library**
  (a user-chosen folder), and places **absolute symlinks it recognises as its own** into each emulator source's ROM folder
  for the system (a multi-file game as its whole folder). ES-DE follows those links. So a downloaded game already shows
  up in RetroDECK's ES-DE. What is missing is everything in the Goal above that is about games _not_ downloaded, ES-DE
  metadata and media, and save sync for a start ES-DE makes rather than Tender.
- ADR-0042, Consequences: a game started from a frontend through a link can write its save somewhere different from the
  same game started through Tender (saves beside the game, a different emulator chosen in ES-DE). Save sync for ES-DE
  starts must ask the resolver with **the path and emulator ES-DE uses**, not Tender's own launch choice.
- Save sync already exposes `pre_launch_sync(rom_id)` and `post_exit_sync` (`backend/main.py`), and the session
  finalizer `SessionLifecycleService` (`backend/services/session_lifecycle.py`). Today they are driven by the frontend's
  Steam lifecycle (`frontend/src/utils/launchInterceptor.ts`, `CustomPlayButton`). This mode drives the same backend
  paths from ES-DE instead.
- The vendored resolver (`backend/_vendor/atlas/installations.py`) already reads ES-DE gamelist emulator selections
  (`_gamelist_selections_at`, per-game `altemulator`). Use it to answer "which emulator does ES-DE start for this content
  path"; do not write a reader of our own (ADR-0042 decision 1).
- The backend hosts itself on a loopback port with token and session admission (`backend/host/access.py`,
  `.claude/rules/host.md`). That port is the bridge ES-DE's hook scripts reach.
- RomM floor is **5.3.0** (`MIN_ROMM_VERSION`, ADR-0040). Confirm the Mac mini's RomM meets it before anything else.

## Design

### 1. Library surface setting

A new setting, **Library surface**: `Steam shortcuts` (today's behaviour, default) | `ES-DE` | `Both`. In `ES-DE` mode
the shortcut apply step is skipped and any Tender-owned shortcuts are removed through the existing removal path
(`services/shortcut_removal.py`), never by hand. Everything else — sync, downloads, BIOS, saves, the QAM panel — runs as
it does today.

Keep the fork mergeable: put new behaviour in new modules behind this setting, and touch upstream modules only at seams.

### 2. Placeholders for games not downloaded

ES-DE lists only files that exist in a system folder, so every not-downloaded game needs something there.

- A placeholder is a **Tender-owned symlink** in the source's ROM folder pointing at a **stub file in Tender's store**
  (e.g. `<tender library>/.placeholders/<rom_id>/<real file name>`), carrying the game's real file name and extension
  so ES-DE files it under the right system. This reuses ADR-0042's link ownership: the delete/adoption code already
  recognises a Tender link by its target, so placeholders get the same protection and the same cleanup.
- On download, the link is **retargeted** from the stub to the real file in the library (one atomic replace: write the
  new link beside it, `rename` over). On uninstall, it is retargeted back to the stub.
- Shapes: single file (link to a stub file); multi-disc (a stub `.m3u` with the name ES-DE would show); folder games
  (ADR-0019) need a shape ES-DE lists as a game — decide per shape (see To decide).
- The stub must be recognisable without reading it (path under `.placeholders/`), so no code path ever hands a stub to
  an emulator, the resolver's save questions, adoption, or the "files the user placed" logic (ADR-0042 decision 5).
- Placeholders are created and removed by the library sync, per enabled platform, the same way shortcuts are today.

### 3. ES-DE metadata and media

- **Media**: write RomM's cover (and screenshot/marquee/title where RomM has them) into ES-DE's `downloaded_media`
  tree for the system, named by the game file's base name. ES-DE ties media to the file name, not the gamelist, so this
  works for placeholders and downloaded games alike, and survives a retarget.
- **Gamelist**: write name, description, release date, developer, publisher, genre, players and rating into ES-DE's
  `gamelist.xml` for the system. Upstream retired writing gamelists for core selection (ADR-0012); this is metadata
  only, never `altemulator`.
- **ES-DE rewrites gamelist.xml itself** (favourites, play count, last played, user edits) when it exits. The writer
  must merge, never replace: touch only entries Tender owns, keep every field ES-DE or the user wrote, and never write
  while ES-DE is running. Prefer to write from the library sync and from the ES-DE `quit` hook's aftermath (see §5),
  and leave the file alone otherwise.
- Paths come from RetroDECK's/ES-DE's own settings through the resolver where it answers them; anything it does not
  answer is a (device) fact to establish in Phase 0, not a hard-coded guess.

### 4. The bridge from ES-DE to Tender

ES-DE custom event scripts run **inside RetroDECK's flatpak sandbox** and cannot call the host's Tender process
directly. They can reach the host's loopback, because a flatpak with network access shares the host network namespace.

- Add a narrow set of routes for frontend hooks on the existing loopback server, admitted by a **hook token** that is
  separate from the panel's token, stored in Tender's data folder with mode `0600`, and readable from inside the sandbox
  (verify RetroDECK's filesystem permissions in Phase 0). Follow every rule in `.claude/rules/host.md`, including the
  admission order and the redaction rule; none of them has a mechanical check.
- Routes, keyed by the content path ES-DE passes (resolve it to a `rom_id` through the link's target; refuse anything
  that is not a Tender link):
  - `prepare` — if the game is a placeholder, start its download (and any missing BIOS the platform needs), report
    progress; if downloaded, run the pre-launch save pull. Returns when ready, or with a refusal the hook can show.
  - `finish` — run the post-exit push and the session finalizer for that game.
  - `status` — for the hook to poll during a long download.
- The hook scripts themselves are small bash files Tender installs into ES-DE's scripts folder (`game-start/`,
  `game-end/`, and `quit/` for a final flush), only when `ES-DE` or `Both` is selected, and removes on uninstall or when
  the setting is turned off. They must be **fail-open** (always exit 0, short timeouts on everything except a running
  download), and use only tools present in RetroDECK's runtime (`curl` is expected — verify).
- Turning on "custom event scripts" in ES-DE is a user step; the QAM panel says so and detects when it is off (no hook
  call has ever arrived) rather than writing ES-DE's settings for them.

### 5. Launch flow from ES-DE

1. User picks a game in ES-DE. `game-start` fires with the ROM path, name and system.
2. Hook calls `prepare`. For a placeholder, Tender downloads into the library, retargets the link, and shows progress as
   Steam toasts (the toaster works over RetroDECK in Game Mode). The hook polls `status` until done.
3. Tender runs the save pull for **the emulator ES-DE will start for that path** (resolver, with ES-DE's per-system and
   per-game selection) and returns.
4. ES-DE starts the emulator on the real file. `game-end` fires; hook calls `finish` in the background; Tender pushes
   the save and records the play session (ADR-0018).
5. `quit` hook: full flush of anything pending, bounded.

Failure: if a download fails or is cancelled, the link stays on the stub, `prepare` refuses with a message, and the hook
shows it (ES-DE cannot abort a launch from a script — verify — so the emulator may open on the stub and error; keep the
stub small and obviously not a game so that failure is quick and harmless). If Phase 0 shows that ES-DE does not wait
for `game-start` scripts, switch to the fallback in To decide.

### 6. Storage on the Deck

The library folder is user-chosen (ADR-0042). Downloaded games stay until uninstalled from the QAM panel; an optional
later setting could evict least-recently-played games over a size budget, but that is out of scope here.

## Phase 0 — device facts to establish first (no code)

Each is "(device)": only a run on the Deck in Game Mode can show it. Record each answer in Decisions.

- [ ] RomM on the Mac mini is ≥ 5.3.0 and reachable from the Deck by its Tailscale MagicDNS name on the LAN and off it.
- [ ] Tender 0.33 installs and runs on the Deck alongside RetroDECK; a downloaded game's link appears in ES-DE.
- [ ] ES-DE runs `game-start` scripts synchronously and waits for them before starting the emulator, with no timeout of
      its own (or what the timeout is).
- [ ] Whether a non-zero exit from a `game-start` script stops the launch.
- [ ] The arguments ES-DE passes each event (expected: ROM path, game name, system name, system full name), and whether
      the ROM path is the link path or the resolved target.
- [ ] Where RetroDECK's ES-DE reads custom event scripts, `gamelist.xml` and `downloaded_media`.
- [ ] From inside the RetroDECK sandbox: `curl` exists, `127.0.0.1:<tender port>` is reachable, and Tender's data folder
      is readable.
- [ ] ES-DE lists a symlink to a zero-byte or tiny stub with a valid extension, and how it treats a symlinked folder.
- [ ] Whether ES-DE rewrites `gamelist.xml` on exit even when nothing changed, and whether it keeps fields it does not
      own.

## To decide

- [ ] Placeholder shape for folder games (ADR-0019) and multi-disc games: a stub `.m3u`, ES-DE's "directories
      interpreted as files", or not listed until downloaded.
- [ ] Fallback if ES-DE does not wait for `game-start`: a per-system launch wrapper in a custom `es_systems.xml`
      (`tender-esde-launch %ROM% -- <original command>`), which can download then `exec` and can abort cleanly, at the
      cost of owning a frontend config file — which ADR-0042 rules out upstream and this fork would accept.
- [ ] Whether this divergence from ADR-0042 (Tender writing ES-DE gamelists, media and scripts) gets its own ADR in the
      fork, and whether to offer it upstream as an opt-in.
- [ ] Download progress while ES-DE waits: Steam toasts only, or also ES-DE's screen (nothing ES-DE offers is known).
- [ ] What `Both` means for a game launched from Steam and from ES-DE with different emulators: allowed with the game-page
      warning ADR-0042 already describes, or blocked.
- [ ] Which media types to write (cover only, or cover + screenshot + marquee + title) and from where (RomM, SteamGridDB).
- [ ] Hook token lifetime: generated once at install, or rotated on each backend start (the hook reads it from the file
      each call either way).

## Decisions

(empty — filled as To decide is answered)

## Done when

Each becomes a test seen failing first, or is marked (device).

- With `ES-DE` selected, a library sync creates no Steam shortcuts and removes existing Tender shortcuts through the
  removal path.
- Every RomM game in an enabled platform has a Tender-owned link in the source's system folder; not-downloaded games
  point at a stub under `.placeholders/`, downloaded games at the library file.
- Download retargets the link atomically; uninstall retargets it back; a failed download leaves the stub link intact.
- No code path passes a stub to an emulator, the resolver's save questions, adoption, or user-placed-file handling.
- Media files exist for each game under ES-DE's media tree, named to match the game file.
- The gamelist writer changes only Tender-owned entries, keeps every other field and entry byte-for-byte, and refuses to
  write while ES-DE runs.
- The hook routes refuse a missing or wrong hook token, and any path that is not a Tender link, in the order
  `host.md` sets.
- `prepare` on a placeholder downloads, retargets and returns ready; on a downloaded game it runs the save pull against
  the emulator ES-DE starts for that path.
- `finish` pushes the save and records the session.
- Hook scripts exit 0 on every failure, timeout and missing tool.
- (device) Pick a not-downloaded game in ES-DE: it downloads with a toast, starts, and its save reaches RomM after
  exit; the same game on another RomM client sees that save.
- (device) Off the home network over Tailscale, the same flow works.
- Docs: a user-guide page for ES-DE mode and the affected architecture pages updated in the same change (`CLAUDE.md`,
  "Documentation"); `mise run gate` passes.

## Suggested order of work

1. Phase 0 on the device.
2. Library surface setting, shortcut skip and removal.
3. Placeholder links, retarget on download and uninstall, stub exclusion everywhere.
4. Media writer, then the merge-safe gamelist writer.
5. Hook token and routes on the loopback server.
6. Hook scripts, their install and removal, and the QAM notice for ES-DE's scripts setting.
7. Save sync and session recording wired to `prepare` / `finish`.
8. Device pass, docs, gate.
