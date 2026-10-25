# Spec: ES-DE library mode — the whole RomM library in RetroDECK's ES-DE, downloaded on demand, saves synced

Fork: `ProphetWild/romm-tender`, based on upstream `0.33.0` (`34104a2`). This file is the epic's body; it follows the
repo's own workflow (`CLAUDE.md`, "Where decisions live"), so open questions sit under `## To decide` and nothing merges
until each is checked off into `## Decisions`.

## Goal

Play the full RomM library from the ES-DE that RetroDECK ships, on a Steam Deck, with no Steam shortcuts:

1. Every RomM game in the enabled platforms appears in ES-DE, whether downloaded or not, with RomM's metadata and every
   kind of media ES-DE shows that RomM has for it — 3D box, marquee, screenshot, title screen, physical media, video and
   the rest — not only the 2D cover.
2. Launching a game that is not downloaded downloads it first, then starts it. No trip to the QAM panel.
3. Saves sync with RomM around every ES-DE launch: pull before start, push after exit.
4. Tender creates no Steam shortcuts in this mode. The QAM panel stays as the settings, download and BIOS surface.

Environment: RomM runs on a Mac mini, reached on the LAN and remotely through Tailscale. The Deck uses one RomM URL (the
Mac mini's MagicDNS name) everywhere. Preparing RomM itself is [esde-romm-server-prompt.md](esde-romm-server-prompt.md).

## What 0.33.0 already gives us

Read these before writing code; each is the current owner of its area.

- **Downloads land in RetroDECK's ROM folders.** `DownloadService` writes a game straight into
  `RetroDeckFolders.download_folder(system)`, at the name `domain/rom_files.py` decides (`resolve_local_file_name`; a
  multi-file game unpacked into a folder that ES-DE's collapse renames to its launch file, `<Game>.m3u/<Game>.m3u`), and
  records it in `rom_installs`. So a downloaded game already shows up in ES-DE as a real file. ADR-0042's own library
  with frontend links is accepted but **not built** in 0.33.0 ("the work waits on resolver answers that do not exist
  yet"); this mode does not wait for it.
- **Rows exist without shortcuts.** The library sync writes a `roms` row for every fetched ROM when a chunk is committed
  (`SyncReporter.commit_unit_results`), bound to a shortcut only where the frontend created one. The commit runs only
  after the frontend acknowledges a `sync_apply_unit` chunk (`ChunkDispatcher.apply_unit_in_chunks`), and `rom_installs`
  references `roms`, so a mode that creates no shortcuts still has to commit its rows.
- Save sync exposes `pre_launch_sync(rom_id)` and, through `SessionLifecycleService.finalize`, the post-exit sync and
  the play session's end; `PlaytimeService.record_session_start` opens a session. Today the frontend's Steam lifecycle
  drives them (`frontend/src/utils/launchInterceptor.ts`, `CustomPlayButton`). They take a `rom_id` only, and the save
  answer is asked with Tender's own emulator resolution (`ActiveCoreResolver`).
- The vendored resolver answers RetroDECK's home folder (`RetroDeck.root()`, `rd_home_path` in `retrodeck.json`, with
  its provenance in `roots()`), and ES-DE reads its gamelists, scripts and media under `<rd_home>/ES-DE/`.
- The backend hosts itself on a loopback port (`backend/host/`, raw asyncio; `.claude/rules/host.md`), with two entry
  points today — files under `dist/` and the WebSocket at `/ws` — and an admission token that lives only in memory.
- Artwork is keyed by ROM, not by shortcut. The only image code Tender carries is the vendored pure-Python `png`
  (`adapters/icon_image.py`): it cannot decode or convert JPEG or WebP, so media is written as RomM serves it.
- RomM floor is **5.3.0** (`MIN_ROMM_VERSION`, ADR-0040); the Mac mini runs 5.4.1 or later after the server prompt.

## Design

New behaviour lives in new modules behind the setting, and touches upstream modules only at seams, so the fork stays
mergeable.

### 1. Library surface setting

A new setting, **Library surface**: `steam` (Steam shortcuts, today's behaviour, the default) | `esde` | `both`.

- `esde`: a library sync commits every chunk without emitting it to the frontend (rows unbound), writes no Steam
  artwork, and runs the ES-DE pass (§2–§4). Turning `esde` on offers to remove Tender's existing Steam shortcuts through
  the existing removal path (`ShortcutRemovalService.remove_all_shortcuts`, driven by the frontend as it is today),
  never by hand.
- `both`: the sync runs as today and the ES-DE pass runs beside it.
- Everything else — downloads, BIOS, saves, the QAM panel — runs as it does today.

### 2. Stand-ins for games not downloaded

ES-DE lists only what exists in a system folder, so every game not downloaded needs something there. A **stand-in** is a
symlink Tender places in RetroDECK's ROM folder for the system, at exactly the path a download of the game would take,
pointing at an empty stub in Tender's cache root (`<cache>/esde/stand-ins/<rom_id>/<name>`). It is Tender's exactly when
its target lies under that folder, and the `<rom_id>` component of the target says which game it stands for.
("Placeholder" already names the empty launch command an uninstalled shortcut gets.)

- **Which games.** One stand-in per sibling-group representative the sync would give a shortcut, on every enabled
  platform — the same set — and none where the game has an install row or where anything Tender does not own already
  lies at that path.
- **The name** is the download's own: `resolve_local_file_name` for a single file; for a multi-file game the collapsed
  folder `<Game>.m3u` (or `<launch file>`) holding an empty `<Game>.m3u`, which ES-DE lists as a directory interpreted
  as a file. ES-DE lists a symlink to a zero-byte file, and a symlink to a folder with a ROM extension, like any other
  game (`SystemData::populateFolder`, source).
- **Download.** The download gate removes the game's own stand-in before it judges the target, so the stand-in never
  reads as an occupied target; adoption's search and the game page's probe skip every stand-in, so a sibling's never
  reads as a file the user placed. A single file then lands at the stand-in's path; a multi-file game lands at it
  whenever the prediction held.
- **A wrong prediction.** A single zip that may hold disc images is unpacked or kept only once its members are seen, so
  its final name can differ from its stand-in's. Then the launch ES-DE already began fails on the stub, a toast says the
  game is downloaded and ES-DE must be restarted to list it, and the next ES-DE pass lists the real file.
- **Uninstall** puts the stand-in back once the install record is dropped (one uninstall, Uninstall all, Forget this
  download).
- **Removal.** The pass removes a stand-in whose game is no longer synced on a platform it read, or whose platform is
  switched off; it never removes anything else. What it placed is recorded in `<data>/esde/index.json`.
- **No stub reaches anything but ES-DE.** A stand-in is never an install, so no save question, adoption, verification or
  launch of Tender's sees it.

### 3. ES-DE media

Facts below are from ES-DE 3.5.0's `USERGUIDE.md` ("Manually copying game media files", "Game media viewer", "Miximage
settings"), `FAQ.md`, `THEMES.md`, `INSTALL.md` ("Custom event scripts") and, where the docs are silent, its source
(`es-app/src/FileData.cpp`, `MiximageGenerator.cpp`, `scrapers/Scraper.cpp`, `views/GamelistView.cpp`,
`es-core/src/Scripting.cpp`), which RetroDECK's `retrodeck-main` ES-DE build carries unchanged in every file cited; from
RomM 5.3.0–5.4.1's source (`backend/config/config_manager.py`, `backend/handler/filesystem/resources_handler.py`,
`backend/endpoints/responses/rom.py`, `backend/utils/gamelist_exporter.py`); and from RetroDECK's
`config/retrodeck/retrodeck.json`, `net.retrodeck.retrodeck.yml` and its ES-DE component scripts. Where a fact matters
on the device, Phase 0 checks it there.

#### 3a. The media types

ES-DE reads twelve folders under `<rd_home>/ES-DE/downloaded_media/<system>/`. Tender fills the eleven RomM can and
never touches `custom`. RomM is the only source (D2). A `*_path` in the RomM field column is read from `ss_metadata`,
then `gamelist_metadata`, then `launchbox_metadata`, the first that is set — the order RomM's own ES-DE exporter takes
(`_collect_assets`); the other fields sit on the ROM itself. The `scan.media` column is the value RomM's server config
must list before RomM stores that media at all.

| ES-DE folder    | What it is               | RomM field                                         | `scan.media`                 | When         | Shown by                                                                             |
| --------------- | ------------------------ | -------------------------------------------------- | ---------------------------- | ------------ | ------------------------------------------------------------------------------------ |
| `covers`        | Front of the box         | `path_cover_large`, else `path_cover_small`        | always stored                | sync         | RetroDECK theme (carousel), every theme's `image` as a last resort, the media viewer |
| `marquees`      | Wheel / clear logo       | `logo_path`, else `gamelist_metadata.marquee_path` | `logo`                       | sync         | RetroDECK theme (grid, textlist, system view), Modern; a miximage input              |
| `screenshots`   | Gameplay                 | the first gameplay entry of `merged_screenshots`   | `screenshot` (on by default) | sync         | Every theme's `image`; a miximage's one required input                               |
| `titlescreens`  | Title screen             | `title_screen_path`                                | `title_screen`               | sync         | RetroDECK theme (grid), the media viewer                                             |
| `3dboxes`       | 3D box                   | `box3d_path`                                       | `box3d`                      | sync         | A miximage input; third-party themes                                                 |
| `backcovers`    | Back of the box          | `box2d_back_path`                                  | `box2d_back`                 | sync         | The media viewer                                                                     |
| `physicalmedia` | Cartridge, disc, tape    | `physical_path`                                    | `physical`                   | sync         | RetroDECK theme (carousel); a miximage input                                         |
| `fanart`        | Background art           | `fanart_path`                                      | `fanart`                     | sync         | The media viewer                                                                     |
| `miximages`     | A composite of the above | `miximage_path`                                    | `miximage`                   | sync         | Every theme's `image` first; RetroDECK theme (textlist, grid)                        |
| `videos`        | Gameplay video           | `path_video`                                       | `video`                      | on selection | RetroDECK theme, Modern, Slate, Linear's video variants                              |
| `manuals`       | Manual, PDF only         | `path_manual`                                      | `manual` (on by default)     | on selection | The media viewer                                                                     |
| `custom`        | The user's own image     | never written                                      | —                            | —            | The media viewer                                                                     |

- RomM's `ss_metadata.marquee_path` is ScreenScraper's screen marquee, not ES-DE's: ES-DE's marquee is the wheel, which
  RomM keeps in `logo_path`. RomM's own exporter maps it the same way.
- A theme's `image` is the first of miximage, screenshot, title screen and cover that exists (`THEMES.md`), so a game
  with only a cover still has an image everywhere.
- RomM's default `scan.media` is `box2d, screenshot, manual`; the other types need the server prompt's change and an
  "Update Metadata" rescan.
- `merged_screenshots` is every source's screenshots merged, and RomM's libretro source puts its title and logo images
  in among them; the entry taken is the first whose path lies under the ROM's own `screenshots/` resources folder, else
  the first.
- RomM's `bezel` and `box2d_side` have no ES-DE folder and are not written.

#### 3b. Fetching from RomM

- The `/api/roms` listing the sync already walks carries every field above, so knowing what media a game has costs no
  extra request.
- `path_cover_*` and `merged_screenshots` entries are absolute server paths under `/assets/romm/resources/`; every other
  `*_path` is relative and takes that prefix in front.
- Only files RomM serves are fetched: never a `*_url` (ScreenScraper URLs need the server's account; gamelist and
  LaunchBox ones are files on the server).
- Every media fetch goes through `RommHttpAdapter.download_conditional`, the cover's own: a missing file's 404 is never
  `RommNotFoundError` and never marks RomM unreachable, the configured custom headers go with it, and the ETag and
  `Last-Modified` it answers are kept for the next revalidation.
- A file is fetched again only when its RomM path or the ROM's `updated_at` changed since it was fetched; an unchanged
  library costs no request.
- **The extension RomM stores is not the format.** Tender sniffs the bytes and names each file by what it is (lower-case
  `.png`, `.jpg`, `.webp`, `.mp4`, `.mkv`, `.webm`, `.pdf`), and writes nothing for bytes ES-DE does not read.
- The sync's media work runs in the background after each platform is committed, a few files at a time, so a first fill
  over a whole library never holds the sync's progress.

#### 3c. Writing into ES-DE's media tree

- **Where**: `<rd_home>/ES-DE/downloaded_media/<system>/<folder>/<stem>.<ext>`. `<stem>` is the entry's name less its
  last extension; a directory ES-DE interprets as a file keeps its whole name (`Final Fantasy VII.m3u/` →
  `Final Fantasy VII.m3u.png`). The entry is the stand-in, or the install's top-level file or folder.
- **The folders are built from RetroDECK's home** (D13): `<rd_home>/ES-DE/{downloaded_media,gamelists,scripts}`, only
  while the resolver's installation root is `read`, never `assumed`, in one adapter
  (`RetroDeckFoldersAdapter.esde_folders`, joined in `domain/esde_folders.py`).
- **Ownership** (D11): the files live in Tender's cache root (`<cache>/esde/media/<rom_id>/<folder>.<ext>`), and the
  media tree gets an absolute symlink to each, Tender's exactly when its target lies there. ES-DE follows a symlinked
  media file (`std::filesystem::exists`, and the texture cache resolves links; source).
- **Never over the user's media.** A media file Tender did not link is never overwritten, renamed or removed, and where
  one exists for a game and folder under any extension ES-DE reads, Tender links nothing for that slot.
- **One file per game and folder.** ES-DE takes the first of `.png`, `.jpg` and `.webp` that exists, so when the
  extension of a link of Tender's changes, Tender removes its old link in the same step.
- **Restart.** ES-DE reads media when a game is selected, so a new link shows the next time the game is selected; a
  replaced file can keep showing the old image until ES-DE restarts (its texture cache).
- **Removal**: when a game's stand-in goes, or the game leaves the sync, its media links go, and nothing else does.

#### 3d. Videos and manuals on selection (D4)

- They stay on RomM. ES-DE's `game-select` event (fired once the cursor stops on a game, detached, so scrolling never
  waits) asks Tender for that game's video and manual; Tender fetches each into `<cache>/esde/on-demand/` and links it.
  ES-DE looked the game's media up just before the event fired, so a fetched video plays from the next time the game is
  selected.
- The on-demand cache is capped (8 GiB); the least recently selected files go first, links with them.
- `game-select` needs ES-DE's "Enable browsing custom events" beside "Enable custom event scripts" (both off by
  default); without it there are no videos or manuals, and nothing else changes.

### 4. Gamelist

- Tender writes `name`, `desc`, `releasedate`, `developer`, `publisher`, `genre` and `players` into
  `<rd_home>/ES-DE/gamelists/<system>/gamelist.xml` for each entry it owns (its stand-ins and its installs), from
  `domain/rom_metadata_mapping.py`'s reading of the ROM: summary, release date, genres, player count, and the first and
  second company as developer and publisher. Never `altemulator`, never a media tag: ES-DE matches media by file name
  and reads no media path from a gamelist (`FAQ.md`).
- **Merge, never replace.** The writer changes only Tender's entries, and in them only its own fields; every other
  entry, and every other field, stays byte for byte. A field whose value differs from what Tender last wrote there was
  edited by the user or ES-DE and is left alone (`<data>/esde/gamelists.json` records what Tender wrote).
- **Never while ES-DE runs.** The sync writes when no `es-de` process runs; otherwise the write waits for ES-DE's
  `startup` event, which fires before ES-DE reads any gamelist and holds ES-DE until its scripts finish.

### 5. The bridge from ES-DE to Tender

ES-DE runs **inside RetroDECK's flatpak**, with `--share=network` and `--filesystem=host`
(`net.retrodeck.retrodeck.yml`): it reaches the host's loopback and reads anything under the home folder by absolute
path, but not `/run`, where Tender's port file lives.

- **Hook routes.** A third entry point on the host, `GET /hooks/esde/<event>`, with arguments in the query string (the
  host parses no body). It is admitted by the same checks in the same order as the other two (Host, then Origin, then
  token), against a **hook token** that admits only `/hooks/` — never `/ws` or the panel's files — and the panel's token
  admits no hook. The hook token is minted at every backend start (D10) and written with the port to
  `<data>/esde-hooks/` (mode 0700) as two files, `port` and `header` (each 0600; `header` holds the one header line
  `curl -H @file` sends), which the scripts read on every call; it is redacted from the log like the admission token.
- **Events.** `startup` (pending gamelist writes, bounded), `game-start` → `prepare`, `game-end` → `finish`,
  `game-select` → `select`. ES-DE passes `game-start` and `game-end` the ROM path with shell backslash escapes still in
  it, and `game-select` the plain path; it passes a link's own path, never its target, and for a directory interpreted
  as a file the file inside it (`Scripting::fireEvent`, `FileData::launchGame`; source).
- **Resolving the game.** A path is Tender's when it, or the directory it is inside, is a stand-in (the target names the
  game) or is an install's recorded file or folder. Anything else is refused and the script does nothing.
- **Scripts.** Small POSIX `sh` files Tender writes into `<rd_home>/ES-DE/scripts/<event>/tender-romm.sh` while the
  surface is `esde` or `both`, and removes when it is `steam`. They read `esde-hooks/port` and `esde-hooks/header`, call
  the route with `curl` (in RetroDECK's runtime), and **always exit 0**; `game-end` backgrounds its call, so leaving a
  game never waits on the upload. ES-DE ignores a script's exit code and cannot cancel a launch from one.
- **Turning scripts on** is a user step in ES-DE (Main menu → Other settings → "Enable custom event scripts" and "Enable
  browsing custom events"); the ES-DE settings section says so and shows when ES-DE last called.

### 6. Launch flow from ES-DE

1. The user starts a game in ES-DE. `game-start` fires; ES-DE waits for its scripts with no timeout before it starts the
   emulator (`FileData::launchGame`; source).
2. `prepare`: for a stand-in, Tender downloads the game (toasts say so), then — for any game of Tender's — opens the
   play session and runs the pre-launch save pull, and answers. A failed download, a refused save pull or an offline
   RomM is said in a toast; the launch goes on.
3. ES-DE starts the emulator on the path it listed, which now holds the game.
4. `game-end` fires; `finish` records the session's end and runs the post-exit save push
   (`SessionLifecycleService.finalize`), and toasts its result.

### 7. Storage on the Deck

Downloaded games stay until uninstalled from the QAM panel. Media lives in the cache root; the on-demand cache is capped
(§3d). Eviction of downloaded games is out of scope.

## Phase 0 — device facts to establish (no code needed)

Each is "(device)": only a run on the Deck in Game Mode can show it. Source reading gave the expected answer; record
what the device shows in Decisions.

- [ ] RomM on the Mac mini is ≥ 5.3.0 and reachable from the Deck by its Tailscale MagicDNS name on the LAN and off it.
- [ ] From inside the RetroDECK sandbox `curl` reaches `127.0.0.1:<tender port>` and reads `<data>/esde-hooks/port` and
      `<data>/esde-hooks/header` (expected: yes, shared network and `--filesystem=host`).
- [ ] ES-DE holds a launch until a `game-start` script exits, with no timeout of its own (expected: yes).
- [ ] The arguments each event passes (expected: escaped link path, name, system, full name for `game-start`/`game-end`;
      plain path for `game-select`).
- [ ] RetroDECK's ES-DE reads scripts, gamelists and media under `<rd_home>/ES-DE/` (expected: yes; RetroDECK links its
      config folder's copies there).
- [ ] ES-DE lists a symlink to a zero-byte stub and a symlink to a folder named `<Game>.m3u` holding a stub `<Game>.m3u`
      (expected: both listed).
- [ ] ES-DE shows a symlinked media file; a video linked after `game-select` plays the next time the game is selected
      (expected: both).
- [ ] ES-DE keeps a gamelist written by the `startup` script and keeps fields it does not own (expected: yes).
- [ ] RomM on the Mac mini collects every type in §3a after the server prompt, and which `merged_screenshots` entry is a
      gameplay shot.

## To decide

- [x] Placeholder shape for folder games and multi-disc games → D5
- [x] Fallback if ES-DE does not wait for `game-start` → D6
- [x] Whether this divergence from ADR-0042 gets its own ADR in the fork → D7
- [x] Download progress while ES-DE waits → D8
- [x] What `Both` means for a game launched from Steam and from ES-DE with different emulators → D9
- [x] Hook token lifetime → D10
- [x] Which media types are on by default → D3
- [x] Videos and manuals: every game or only downloaded ones → D4
- [x] Miximages → D12
- [x] SteamGridDB as a fallback → D2
- [x] Media ownership → D11
- [x] WebP on an older ES-DE → D1
- [x] How the resolver comes to answer ES-DE's media folder → D13
- [x] Which emulator the save sync asks about for an ES-DE start → D14
- [x] Which games get a stand-in → D15
- [x] When the gamelist is written → D16

## Decisions

Answered by the owner on 2026-10-25: D1–D4. Taken on 2026-10-25 under the owner's instruction to build without a device
at hand, each to be confirmed or struck after the device pass: D5–D16.

- **D1** — The surface is RetroDECK's own ES-DE (its flatpak and its ES-DE build), current releases only. WebP media is
  written as RomM serves it; nothing is converted.
- **D2** — RomM is the only source of ES-DE media. SteamGridDB is not used in this mode: it is Steam's artwork, and
  where its images are wanted RomM's own SteamGridDB provider brings them in on the server.
- **D3** — Every image type is on, with no per-type switch.
- **D4** — Videos stay on RomM and reach the Deck on demand: fetched when ES-DE selects a game, into a capped cache,
  played from the next selection. Manuals are fetched the same way.
- **D5** — A stand-in takes the shape the download will produce, predicted by the download's own naming functions; a
  multi-file game's is a folder ES-DE interprets as a file. Where the prediction fails (a zip whose members decide),
  that one launch fails on the stub with a toast, and the next ES-DE start lists the real game.
- **D6** — No launch wrapper: ES-DE waits for `game-start` scripts with no timeout (source), so `prepare` downloads
  inside the hook. Phase 0 confirms it.
- **D7** — An ADR in the fork records the divergence from ADR-0042 (Tender writes ES-DE's gamelists, media links,
  stand-ins and scripts); it is offered upstream only after the device pass.
- **D8** — Download progress is shown by Steam toasts only; ES-DE offers a script no way to draw on its screen.
- **D9** — `Both` is allowed: a game has a shortcut and an ES-DE entry, and ADR-0042's warning about different emulators
  writing saves to different places stands.
- **D10** — The hook token is minted at every backend start and written, with the port, to 0600 files in the data root
  (`<data>/esde-hooks/port` and `<data>/esde-hooks/header`); the scripts read them on every call.
- **D11** — Media files live in the cache root and reach ES-DE as symlinks Tender recognises by their target.
- **D12** — Miximages are RomM's (ScreenScraper's), where RomM has one; otherwise ES-DE's own offline generator can
  build them from what Tender linked.
- **D13** — ES-DE's `downloaded_media`, `gamelists` and `scripts` folders are built from the resolver's RetroDECK home,
  only while that root was read, in one adapter. It is the fork's one exception to "every RetroDECK folder is the
  resolver's answer", until emu-atlas answers these folders; a `downloaded_media` moved with RetroDECK's Configurator is
  not followed.
- **D14** — The first cut asks the save sync with Tender's own emulator resolution, not ES-DE's choice: three sites
  hard-wire it (`RomInfoService._ask_resolver`, `SyncEngine.resolve_core`, core-change detection). A game started in
  ES-DE with another emulator than Tender's can sync the wrong save place; the ES-DE settings section says so.
- **D15** — Stand-ins are placed for the sibling-group representatives the sync would give shortcuts, as shortcuts are;
  other versions appear in ES-DE once downloaded.
- **D16** — The gamelist is written at the end of a sync when ES-DE is not running, and otherwise from ES-DE's `startup`
  event, which fires before ES-DE reads any gamelist.

## Done when

Each becomes a test seen failing first, or is marked (device).

- With `esde` selected, a library sync emits no `sync_apply_unit`, commits every fetched ROM, and writes no Steam
  artwork; turning `esde` on offers the existing removal of Tender's shortcuts.
- Every representative on an enabled platform without an install row has a stand-in at the path its download would take;
  nothing Tender does not own is replaced.
- A download of a game with a stand-in succeeds and leaves the real file at the stand-in's path; a sibling's stand-in
  never reads as a file the user placed; uninstall and forget put the stand-in back.
- No stand-in is handed to a save question, adoption, verification or a launch.
- Every image type RomM has for a game is linked at `<system>/<folder>/<stem>.<ext>`; the extension is what the bytes
  are; a media file Tender did not link is never touched, and a slot that has one gets nothing.
- A media fetch's 404 is never `RommNotFoundError`, and an unchanged library costs no media request.
- `select` fetches the video and the manual into the capped cache and links them.
- The gamelist writer changes only Tender's fields in Tender's entries, keeps everything else byte for byte, keeps a
  field the user edited, and writes nothing while ES-DE runs.
- The hook routes refuse a missing or wrong hook token, the panel's token refuses a hook, the hook token refuses `/ws`,
  and a path that is not Tender's is refused.
- `prepare` on a stand-in downloads and runs the save pull; `finish` records the session and pushes the save.
- Every hook script exits 0 on every failure, timeout and missing tool.
- (device) Pick a not-downloaded game in ES-DE: it downloads with a toast, starts, and its save reaches RomM after exit.
- (device) Off the home network over Tailscale, the same flow works.
- Docs: a user-guide page for ES-DE mode, an architecture page, the glossary's terms and the fork's ADR in the same
  change; the backend and frontend suites pass.

## Order of work

1. [x] The setting, the sync's ES-DE branch (commit without emitting, no Steam artwork), and the shortcut removal offer.
2. [x] ES-DE folders, stand-ins, and their seams in downloads, uninstall, adoption and the game page.
3. [x] Media: the mapping, the fetch, the links; then on-demand videos and manuals.
4. [x] The merge-safe gamelist writer.
5. [x] The hook token, the hook routes, the hook service (`prepare`, `finish`, `select`, `startup`) and the scripts.
6. [x] The ES-DE settings section and the toasts.
7. [x] Docs, the ADR, the suites ([ES-DE Mode](../user-guide/es-de-mode.md),
       [ES-DE Library Mode](../architecture/es-de-library.md), [ADR-0044](../adr/0044-es-de-library-mode.md)).
8. [ ] The device pass (Phase 0 and the "(device)" criteria above).
