# Spec: ES-DE library mode — the whole RomM library in ES-DE, downloaded on demand, saves synced

Fork: `ProphetWild/romm-tender`, based on upstream `0.33.0` (`34104a2`). This file is the epic's body; it follows the
repo's own workflow (`CLAUDE.md`, "Where decisions live"), so open questions sit under `## To decide` and nothing merges
until each is checked off into `## Decisions`.

## Goal

Play the full RomM library from ES-DE inside RetroDECK on a Steam Deck, with no Steam shortcuts:

1. Every RomM game in the enabled platforms appears in ES-DE, whether downloaded or not, with RomM's metadata and every
   kind of media ES-DE shows that RomM (or SteamGridDB) has for it — 3D box, marquee, screenshot, title screen, physical
   media and the rest — not only the 2D cover.
2. Launching a game that is not downloaded downloads it first, then starts it. No trip to the QAM panel.
3. Saves sync with RomM around every ES-DE launch: pull before start, push after exit.
4. Tender creates no Steam shortcuts in this mode. The QAM panel stays as the settings, download and BIOS surface.

Environment: RomM runs on a Mac mini, reached on the LAN and remotely through Tailscale. The Deck uses one RomM URL (the
Mac mini's MagicDNS name) everywhere.

## What upstream already gives us

Read these before writing code; each is the current owner of its area.

- **ADR-0042** (`docs/adr/0042-…`): Tender starts emulators itself, keeps downloaded games in **Tender's own library**
  (a user-chosen folder), and places **absolute symlinks it recognises as its own** into each emulator source's ROM
  folder for the system (a multi-file game as its whole folder). ES-DE follows those links. So a downloaded game already
  shows up in RetroDECK's ES-DE. What is missing is everything in the Goal above that is about games _not_ downloaded,
  ES-DE metadata and media, and save sync for a start ES-DE makes rather than Tender.
- ADR-0042, Consequences: a game started from a frontend through a link can write its save somewhere different from the
  same game started through Tender (saves beside the game, a different emulator chosen in ES-DE). Save sync for ES-DE
  starts must ask the resolver with **the path and emulator ES-DE uses**, not Tender's own launch choice.
- Save sync already exposes `pre_launch_sync(rom_id)` and `post_exit_sync` (`backend/main.py`), and the session
  finalizer `SessionLifecycleService` (`backend/services/session_lifecycle.py`). Today they are driven by the frontend's
  Steam lifecycle (`frontend/src/utils/launchInterceptor.ts`, `CustomPlayButton`). This mode drives the same backend
  paths from ES-DE instead.
- The vendored resolver (`backend/_vendor/atlas/installations.py`) already reads ES-DE gamelist emulator selections
  (`_gamelist_selections_at`, per-game `altemulator`). Use it to answer "which emulator does ES-DE start for this
  content path"; do not write a reader of our own (ADR-0042 decision 1).
- The backend hosts itself on a loopback port with token and session admission (`backend/host/access.py`,
  `.claude/rules/host.md`). That port is the bridge ES-DE's hook scripts reach.
- Artwork today is keyed by ROM, not by shortcut, so this mode can take it as it is. The library sync caches RomM's
  cover per ROM in the cache root (`{rom_id}.png`, `domain/artwork_paths.py`) by a conditional GET that revalidates by
  ETag (`RommApiAdapter.download_cover` over `RommHttpAdapter.download_conditional`), falling back to the ROM's
  `url_cover` on a 404 (`services/artwork/service.py`). The SteamGridDB pipeline (`services/steamgrid/`,
  `domain/sgdb_artwork.py`) caches a cover, a logo, a hero, a wide grid and an icon per ROM. The only image code Tender
  carries is the vendored pure-Python `png` (`adapters/icon_image.py`): it cannot decode or convert JPEG or WebP.
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
  (e.g. `<tender library>/.placeholders/<rom_id>/<real file name>`), carrying the game's real file name and extension so
  ES-DE files it under the right system. This reuses ADR-0042's link ownership: the delete/adoption code already
  recognises a Tender link by its target, so placeholders get the same protection and the same cleanup.
- On download, the link is **retargeted** from the stub to the real file in the library (one atomic replace: write the
  new link beside it, `rename` over). On uninstall, it is retargeted back to the stub.
- Shapes: single file (link to a stub file); multi-disc (a stub `.m3u` with the name ES-DE would show); folder games
  (ADR-0019) need a shape ES-DE lists as a game — decide per shape (see To decide).
- The stub must be recognisable without reading it (path under `.placeholders/`), so no code path ever hands a stub to
  an emulator, the resolver's save questions, adoption, or the "files the user placed" logic (ADR-0042 decision 5).
- Placeholders are created and removed by the library sync, per enabled platform, the same way shortcuts are today.

### 3. ES-DE metadata and media

Facts below are from ES-DE 3.5.0's `USERGUIDE.md` ("Manually copying game media files", "Game media viewer", "Miximage
settings"), `FAQ.md`, `THEMES.md` and, where the docs are silent, its source (`es-app/src/FileData.cpp`,
`MiximageGenerator.cpp`, `scrapers/Scraper.cpp`); from RomM 5.3.0–5.4.1's source (`backend/config/config_manager.py`,
`backend/handler/filesystem/resources_handler.py`, `backend/endpoints/responses/rom.py`,
`backend/utils/gamelist_exporter.py`); and from RetroDECK's `config/retrodeck/retrodeck.json` and its ES-DE component
scripts. Where a fact matters on the device, Phase 0 checks it there.

#### 3a. The media types

ES-DE reads twelve folders under `<MediaDirectory>/<system>/`. Tender fills the eleven RomM can and never touches
`custom`. A `*_path` in the RomM field column is read from `ss_metadata`, then `gamelist_metadata`, then
`launchbox_metadata`, the first that is set — the order RomM's own ES-DE exporter takes (`_collect_assets`); the other
fields sit on the ROM itself. The `scan.media` column is the value RomM's server config must list before RomM stores
that media at all.

| ES-DE folder    | What it is               | RomM field                                         | `scan.media`                 | Fallback                                | Shown by                                                                             |
| --------------- | ------------------------ | -------------------------------------------------- | ---------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------ |
| `covers`        | Front of the box         | `path_cover_large`, else `path_cover_small`        | always stored                | SteamGridDB cover (Tender's pick today) | RetroDECK theme (carousel), every theme's `image` as a last resort, the media viewer |
| `marquees`      | Wheel / clear logo       | `logo_path`, else `gamelist_metadata.marquee_path` | `logo`                       | SteamGridDB logo                        | RetroDECK theme (grid, textlist, system view), Modern; a miximage input              |
| `screenshots`   | Gameplay                 | an entry of `merged_screenshots`                   | `screenshot` (on by default) | —                                       | Every theme's `image`; a miximage's one required input                               |
| `titlescreens`  | Title screen             | `title_screen_path`                                | `title_screen`               | —                                       | RetroDECK theme (grid), the media viewer                                             |
| `3dboxes`       | 3D box                   | `box3d_path`                                       | `box3d`                      | —                                       | A miximage input; third-party themes                                                 |
| `backcovers`    | Back of the box          | `box2d_back_path`                                  | `box2d_back`                 | —                                       | The media viewer                                                                     |
| `physicalmedia` | Cartridge, disc, tape    | `physical_path`                                    | `physical`                   | —                                       | RetroDECK theme (carousel); a miximage input                                         |
| `fanart`        | Background art           | `fanart_path`                                      | `fanart`                     | SteamGridDB hero                        | The media viewer                                                                     |
| `miximages`     | A composite of the above | `miximage_path`                                    | `miximage`                   | ES-DE's own generator (see To decide)   | Every theme's `image` first; RetroDECK theme (textlist, grid)                        |
| `videos`        | Gameplay video           | `path_video`                                       | `video`, `video_normalized`  | —                                       | RetroDECK theme, Modern, Slate, Linear's video variants                              |
| `manuals`       | Manual, PDF only         | `path_manual`                                      | `manual` (on by default)     | —                                       | The media viewer                                                                     |
| `custom`        | The user's own image     | never written                                      | —                            | —                                       | The media viewer                                                                     |

- RomM's `ss_metadata.marquee_path` is ScreenScraper's screen marquee, not ES-DE's: ES-DE's marquee is the wheel, which
  RomM keeps in `logo_path`. RomM's own exporter maps it the same way.
- A theme's `image` is the first of miximage, screenshot, title screen and cover that exists (`THEMES.md`). So a game
  with a cover and nothing else still has an image everywhere, and a screenshot or miximage replaces the cover in most
  layouts.
- RomM's default `scan.media` is `box2d, screenshot, manual`. Out of the box, then, RomM has covers, screenshots and
  manuals and nothing else on this table: the other types need the Mac mini's `scan.media` widened, a metadata source
  that has them (ScreenScraper for most; LaunchBox gives the box faces from RomM 5.4), and an "Update Metadata" rescan
  (Phase 0).
- `merged_screenshots` is every source's screenshots merged, and RomM's libretro source puts its title and logo images
  in among them; which entry to take for `screenshots` is read off real data in Phase 0, not assumed to be the first.
- RomM's `bezel` and `box2d_side` have no ES-DE folder and are not written.

#### 3b. Fetching from RomM

- The `/api/roms` listing the sync already walks carries every field above (each item is RomM's whole `RomSchema`), so
  knowing what media a game has costs no extra request.
- `path_cover_*` and `merged_screenshots` entries are absolute server paths under `/assets/romm/resources/`; every other
  `*_path` is relative and takes that prefix in front. RomM's nginx serves the prefix without the bearer token.
- Only files RomM serves are fetched. Never a `ss_metadata.*_url` (a ScreenScraper URL that needs the server's
  ScreenScraper account), a `gamelist_metadata.*_url` (a `file://` URI on the server) or a `launchbox-file://` one.
- Every media fetch is a byte-stream fetch through the conditional download the cover already uses
  (`RommHttpAdapter.download_conditional`). That keeps three rules without a new site to get wrong: a missing file's 404
  is never `RommNotFoundError`, which downstream is deletion authority (`.claude/rules/romm-http.md`), and never marks
  RomM unreachable; the request goes out through `_urlopen` with the configured custom headers, as every RomM-origin
  request does; and a resync revalidates each file by ETag or `Last-Modified`, which matters because only the cover's
  path carries a `?ts=` cache-buster.
- **The extension RomM stores is not the format.** RomM writes the provider's bytes under a fixed `.png` or `.mp4` name
  and checks only the content type. Tender sniffs the bytes, names each file by what it is (lower-case `.png`, `.jpg`,
  `.webp`, `.mp4`, `.pdf`), and writes nothing for bytes ES-DE does not read. Tender cannot convert: a WebP for an ES-DE
  without WebP support (Phase 0) is dropped, not turned into a PNG, unless To decide says otherwise.
- The SteamGridDB fallbacks come from the existing per-ROM cache and pipeline, so they need the user's key and stop at
  its rate limit (`RATE_LIMITED`); a first fill over a whole library is paced, never a burst.

#### 3c. Writing into ES-DE's media tree

- **Where**: `<MediaDirectory>/<system>/<folder>/<subfolders>/<stem>.<ext>`. The type folder comes before the ROM
  folder's subfolders, which the media tree mirrors. `<stem>` is the frontend link's name less its last extension; a
  directory ES-DE interprets as a file keeps its whole name (`Final Fantasy VII.m3u/` → `Final Fantasy VII.m3u.png`). So
  the media name follows the placeholder shape chosen under To decide, and media written for one shape is renamed when a
  game changes shape. Because a download only retargets the link, its media stays put.
- **The media folder is the resolver's answer.** RetroDECK writes ES-DE's `MediaDirectory` from `downloaded_media_path`
  in `retrodeck.json` (default `~/retrodeck/ES-DE/downloaded_media`), and its Configurator can move it. The vendored
  resolver does not answer it today, and the invariant register requires every RetroDECK folder Tender writes into to be
  the resolver's answer through `adapters/retrodeck_folders.py`, with no module of ours reading RetroDECK's settings. So
  the resolver has to grow that answer (To decide), and a root it only assumed is used for nothing, as for every other
  RetroDECK folder.
- **One file per game and type.** ES-DE takes the first of `.png`, `.jpg` and `.webp` that exists
  (`FileData::getMediafilePath`), so a stale `.png` hides a new `.jpg`. When the extension of a file of Tender's
  changes, Tender removes its old file in the same step, as ES-DE's own scraper does.
- **Never over the user's media.** RetroDECK ships with ES-DE's scraper on, so a user may already have media there. A
  media file Tender did not write is never overwritten, renamed or removed, and where one exists for a game and type
  under any extension ES-DE reads, Tender writes nothing for that slot.
- **Ownership** (proposal, To decide): the files live in Tender's cache root, since RomM can serve them again, and the
  media tree gets an absolute symlink to each, Tender's own exactly when its target is in that cache — the rule frontend
  links follow. A regular file in the media tree is never Tender's. If Phase 0 shows ES-DE does not follow a symlinked
  media file, the fallback is regular files, each recorded with its digest and removed only while the digest still
  matches.
- **Write before ES-DE starts.** Modern's and Slate's `noMedia` trigger is judged once per system, when ES-DE builds the
  view, and switches a system with no miximage, screenshot or cover to a layout without media until ES-DE restarts.
  ES-DE also caches textures by path, so a file replaced in place can keep showing the old image until restart. The
  library sync writes media and the hooks never do, and the QAM panel says that ES-DE needs a restart to show a first
  fill.
- **Removal**: when the library sync removes a game's placeholder, Tender's media for that game goes with it, and
  nothing else does.

#### 3d. Size and settings

- One switch per ES-DE folder in the ES-DE section of the settings. Proposed defaults (To decide): every image type on;
  `videos` and `manuals` off, since each runs to megabytes per game and a whole library is thousands of games on the
  Deck's own storage.
- The QAM panel reads RomM's `scan.media` (`GET /api/config`, field `SCAN_MEDIA`) and names each switched-on type the
  server is not set to collect, the way it names ES-DE's custom-event-scripts setting. Tender never changes the server's
  config.

#### 3e. Gamelist

- **Gamelist**: write name, description, release date, developer, publisher, genre, players and rating into ES-DE's
  `gamelist.xml` for the system. Upstream retired writing gamelists for core selection (ADR-0012); this is metadata
  only, never `altemulator`. ES-DE matches media by file name and reads no media path from a gamelist (`FAQ.md`), so the
  writer writes no media tags either. RomM's own gamelist export (`POST /api/export/gamelist-xml`) writes into the
  server's platform folder and returns nothing over HTTP, so the Deck cannot use it.
- **ES-DE rewrites gamelist.xml itself** (favourites, play count, last played, user edits) when it exits. The writer
  must merge, never replace: touch only entries Tender owns, keep every field ES-DE or the user wrote, and never write
  while ES-DE is running. Prefer to write from the library sync and from the ES-DE `quit` hook's aftermath (see §5), and
  leave the file alone otherwise.
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
- [ ] Where RetroDECK's ES-DE reads custom event scripts, `gamelist.xml` and `downloaded_media` (expected: `scripts/`,
      `gamelists/<system>/` and `downloaded_media/` under `<RetroDECK home>/ES-DE/`), and that `downloaded_media_path`
      in `retrodeck.json` equals `MediaDirectory` in ES-DE's `es_settings.xml`.
- [ ] RomM on the Mac mini is set to collect the media this mode writes: its `scan.media` lists at least `box3d`,
      `box2d_back`, `logo`, `title_screen`, `physical` and `fanart` (plus `miximage`, `video` as decided), it has a
      ScreenScraper account configured, and after an "Update Metadata" rescan one ROM's `/api/roms/{id}` shows those
      `ss_metadata.*_path` fields filled. Also which `merged_screenshots` entry is a gameplay screenshot, and whether
      Tender's token may read `GET /api/config`.
- [ ] Which ES-DE build RetroDECK on the Deck runs (WebP media needs ES-DE 3.4 or later), and which theme and variant is
      active.
- [ ] ES-DE shows a media file that is a symlink; shows one added while it runs (expected: the next time the game is
      selected); and shows a file replaced while it runs (expected: the old image until restart).
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
- [ ] What `Both` means for a game launched from Steam and from ES-DE with different emulators: allowed with the
      game-page warning ADR-0042 already describes, or blocked.
- [ ] Which media types are on by default. Proposal: every image type on (`covers`, `marquees`, `screenshots`,
      `titlescreens`, `3dboxes`, `backcovers`, `physicalmedia`, `fanart`, `miximages`); `videos` and `manuals` off.
- [ ] Once switched on, whether videos and manuals are written for every game or only for downloaded ones.
- [ ] Miximages: RomM's ScreenScraper miximage where RomM has one, which looks unlike ES-DE's own; or leave the slot
      empty for ES-DE's offline generator (Scraper → Miximage settings → Offline generator, a GUI step the QAM panel can
      name), which builds one from the screenshot, marquee, 3D box or cover and physical media Tender wrote; or both,
      RomM's first.
- [ ] SteamGridDB as the fallback for `covers`, `marquees` (its logo) and `fanart` (its hero): on whenever a key is set,
      or a switch of its own.
- [ ] Media ownership: symlinks into Tender's cache root (proposal) or regular files recorded with their digests. The
      Phase 0 symlink fact decides it.
- [ ] WebP media from RomM where the Deck's ES-DE is older than 3.4: drop it, or vendor a decoder and convert it to PNG.
- [ ] How the resolver comes to answer ES-DE's media folder: a documented local patch to `_vendor/atlas`
      (`.claude/rules/vendored-assets.md`), or a change upstream in emu-atlas first.
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
- For each switched-on type, every game that RomM (or SteamGridDB, for its three fallbacks) has that media for has
  exactly one file at `<system>/<folder>/<subfolders>/<stem>.<ext>` under ES-DE's media folder, for a placeholder and a
  downloaded game alike.
- Each file's extension is what its bytes are; bytes ES-DE does not read are not written; videos and manuals are written
  only while their switches are on.
- A media fetch's 404 is never `RommNotFoundError` and never marks RomM unreachable, and every media fetch carries the
  configured custom headers.
- A media file Tender did not write is never overwritten, renamed or removed, and a game and type that has one gets
  nothing from Tender.
- No game and type is left with two of Tender's files that differ only by extension.
- The QAM panel names each switched-on type RomM's `scan.media` leaves out.
- When the sync removes a game's placeholder, Tender's media for that game goes, and nothing else in the media tree.
- (device) With RomM collecting them, RetroDECK's theme shows a game's marquee, physical media and screenshot, and the
  media viewer its back cover, title screen and fan art.
- The gamelist writer changes only Tender-owned entries, keeps every other field and entry byte-for-byte, and refuses to
  write while ES-DE runs.
- The hook routes refuse a missing or wrong hook token, and any path that is not a Tender link, in the order `host.md`
  sets.
- `prepare` on a placeholder downloads, retargets and returns ready; on a downloaded game it runs the save pull against
  the emulator ES-DE starts for that path.
- `finish` pushes the save and records the session.
- Hook scripts exit 0 on every failure, timeout and missing tool.
- (device) Pick a not-downloaded game in ES-DE: it downloads with a toast, starts, and its save reaches RomM after exit;
  the same game on another RomM client sees that save.
- (device) Off the home network over Tailscale, the same flow works.
- Docs: a user-guide page for ES-DE mode and the affected architecture pages updated in the same change (`CLAUDE.md`,
  "Documentation"); `mise run gate` passes.

## Suggested order of work

1. Phase 0 on the device, with the Mac mini's `scan.media` widened and its library rescanned first.
2. Library surface setting, shortcut skip and removal.
3. Placeholder links, retarget on download and uninstall, stub exclusion everywhere.
4. Media: the resolver's media-folder answer, the field mapping and the fetch through the conditional download, the
   writer and its ownership rule, the per-type switches and the `scan.media` notice. Then the merge-safe gamelist
   writer.
5. Hook token and routes on the loopback server.
6. Hook scripts, their install and removal, and the QAM notice for ES-DE's scripts setting.
7. Save sync and session recording wired to `prepare` / `finish`.
8. Device pass, docs, gate.
