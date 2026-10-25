# ES-DE Library Mode

ES-DE library mode lists the RomM library in the ES-DE that RetroDECK ships instead of, or beside, Steam's shortcuts.
What the user sees and does is the user guide's ([ES-DE Mode](../user-guide/es-de-mode.md)); the decision to write into
ES-DE's own files at all is [ADR-0044](../adr/0044-es-de-library-mode.md). This page is how it is built: the setting and
the sync's branch, the stand-ins, ES-DE's folders, the media, the gamelists, the hooks ES-DE's event scripts call, and
the status read the settings section shows.

Everything lives in new modules behind the setting — `services/esde/` (a package of seven services consumed through its
`__init__.py`), `domain/esde_*.py`, `adapters/esde_*.py` and `host/hooks.py` — and reaches the rest of the backend only
through Protocols in `services/protocols/` that default to doing nothing. Under the `steam` surface none of it changes
what a sync, a download or an uninstall does.

## The library surface

`library_surface` in `settings.json` is `"steam"` (the default), `"esde"` or `"both"` (`domain/library_surface.py`,
which also holds the two questions asked of a surface: `creates_steam_shortcuts` and `writes_esde`). Any stored value
that names no surface reads as `"steam"`, so a settings file without the key needs no version bump.
`SettingsService.set_library_surface` validates and persists it (`invalid_library_surface` otherwise) and is refused
with `sync_active` while a library sync is in flight, before it judges the value (GLOSSARY.md → Conflict rules): the
switch's refetch and its removals would race the running pass (below).

**The sync reads the surface once,** when an apply run starts, onto the state box (`run_library_surface`), so every unit
of one run applies to the same surface and a change reaches the next run. Under `"esde"`:

- `ChunkDispatcher.apply_unit_in_chunks` stages each unit as before, then commits each chunk through
  `SyncReporter.commit_unit_results` with an empty appId map instead of emitting `sync_apply_unit` and waiting for the
  frontend's ack. Both paths commit through one `_commit_chunk`, so the non-emitting commit carries what the acked
  path's carries for a chunk the frontend bound nothing in: the rows, the platform or collection stamp on the final
  chunk, the fetch generation and the delivery route. Rows land unbound; a binding a row already holds is kept by the
  commit's merge.
- Where nothing is emitted the dispatcher clears the dispatch identity (`active_unit_id`, `active_chunk_index`,
  `unit_complete_event`) before its first commit and never sets it, so no ack can match a unit that was never sent —
  including one carrying the live run id after an earlier run left an identity behind. No chunk is priced against
  Steam's session budget.
- `SyncOrchestrator._sync_one_unit` runs neither cover-preparer step, since both write Steam's grid files.
- The panel's Sync page computes no preview (`useSyncPage`'s `noPreview`): a preview counts shortcut changes, and an
  `"esde"` sync makes none. `"both"` still previews.

Under `"steam"` and `"both"` the emitted path is unchanged.

**A platform the sync skips as unchanged hands the ES-DE pass none of RomM's fields** — its rows are rebuilt from the
sync's own record. A switch to a surface that writes ES-DE from one that does not therefore asks the `skip_revoker`
(`LibrarySkipRevoker`, wired to `LibraryService`) to revoke every skip, once the setting is persisted and before any
listener is told: `SyncReporter.revoke_every_skip` revokes each platform stamp's skip and keeps the stamp
(`PlatformSyncStateRepository.revoke_every_skip`, one `UPDATE`), so removed-game discovery keeps each fetch generation;
a collection stamp has no revoked state and every one is dropped, as Force Full Sync drops them. `esde` to `both` and
back revokes nothing. The skip also needs bound rows to rebuild a unit from
(`LibraryFetcher._try_unit_incremental_skip`), so under `"esde"` alone, where no row is bound, every sync fetches every
platform in full; the media fingerprints below keep that from costing a media request.

## The ES-DE pass

`EsdeLibraryPass` (`services/esde/library_pass.py`) is the one object the sync and the settings hold. It implements
`EsdeStandInPass`, which `SyncOrchestrator` calls, and `LibrarySurfaceListener`, which the settings tell, and hands each
call to the stand-in, media and gamelist services in that order, because a game's media and gamelist entry are named
after the entry the stand-in service settles:

| Call                                  | When the sync makes it                                               | What the pass does                                                                                                                                                      |
| ------------------------------------- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `begin_run(surface)`                  | at an apply run's start                                              | the stand-ins start a run for _surface_; nothing happens in a run whose surface writes no ES-DE                                                                         |
| `place(unit, roms, ids, skipped=...)` | after each unit's commit, and for a unit skipped as unchanged        | places the unit's stand-ins, then queues each representative's media and collects its gamelist fields under the entry the stand-ins answered; a skipped unit only moves |
| `settle()`                            | at the end of a run that was not cancelled, and of one with no units | settles the stand-ins, removes the media of games no longer kept, moves the media of games a download now holds, then writes the gamelists where ES-DE is not running   |
| `surface_stored(surface)`             | each time the settings store a surface, the same one again included  | tells the stand-ins, then the media, then the gamelists                                                                                                                 |

The settings hold a tuple of listeners, told in order: `(EsdeLibraryPass, EsdeHookScriptService)`
(`bootstrap/services.py`). They are told after the setting is persisted and after the skip revocation, so
`services/settings.py` imports nothing of ES-DE. The orchestrator stays at its module-size threshold; the pass is how it
does without growing.

## Stand-ins

ES-DE lists only what lies in a system's folder, so a game that is not downloaded is listed through a **stand-in**: an
absolute symlink at exactly the path its download would take, pointing at an empty stub under the cache root,
`<cache>/esde/stand-ins/<rom_id>/<name>`. A link is Tender's stand-in exactly when its target lies below that folder,
and the target's `<rom_id>` names the game (`domain/esde_stand_in.py`). A multi-file game's stand-in is a link to a stub
folder named like the collapsed download folder, `<Game>.m3u`, holding an empty file of the same name, which ES-DE lists
as a directory interpreted as a file (`SystemData::populateFolder`, es-app/src/SystemData.cpp). "Placeholder" is not
this: it names the empty launch command an uninstalled shortcut carries.

**Which games.** One per sibling-group representative the sync gives a shortcut — the ROMs the unit's delta emitted —
that no download holds (D15); none where the game has an install row, and none where anything that is not this game's
stand-in lies at the path. The system and folder are the download's (`PlatformSystems`,
`RetroDeckFolders.download_folder`).

**The name is predicted by the download's own functions** (`predict_stand_in`), in the order
`DownloadUnpacker.unpack_and_record` runs them, through the naming `domain/rom_files.py` now holds for both
(`coerce_safe_component`, `safe_local_file_name`, `safe_extract_dir_name`, `m3u_playlist_name`): a single file is the
name the download gives it; a multi-file ROM, and a single zip whose listed members hold a disc image, is the folder it
is unpacked into once the playlist is written, the launch file detected and ES-DE's collapse applied; a single zip RomM
lists no members for is the zip itself — the one prediction that can miss (D5). No stand-in where the result is not one
game to ES-DE (a launch file in a subfolder, a folder-boot layout), where a file would land outside the folder, or where
the launch file depends on the order the filesystem lists the folder in. A shape the listing cannot settle — anything
but a simple single non-zip file — asks `get_rom` once and is reused while the ROM's `updated_at` is unchanged.
`tests/domain/test_esde_stand_in_conformance.py` runs every shape through the real unpacker and `DownloadFileAdapter`
and holds the prediction to what landed.

**Ownership.** `EsdeStandInsAdapter` (`adapters/esde_stand_ins.py`, `EsdeStandInStore`) does every file operation. A
path nothing is at takes an exclusive `os.symlink`, which fails rather than replace something that appeared there; only
this game's own link, re-read with `lstat`/`readlink` and never followed, is replaced, through a temporary link renamed
over it, so no reader sees the path empty. Removal re-reads the same way. A file, a folder or anybody else's link at the
path is left exactly as it is.

**The record** is `<data>/esde/index.json` (`adapters/esde_stand_in_index.py`; written atomically; an unreadable one
reads as empty and is logged): per game its platform, system, the stand-in's path and shape or none, and the
`updated_at` the shape was predicted for. It is changed under one lock.

**What a run keeps.** Nothing is removed before a run ends, and only if it was not cancelled. Then the stand-ins of the
run's representatives stay, and so do those of games on platforms skipped as unchanged and of members of skipped
collections on platforms the run did not read; every other stand-in goes, and so does the link of a game a download now
holds (its record stays). `synced_rom_ids` is not the rule, because it counts every version of a game and would keep a
former representative's stand-in beside the new one's. `settle` answers a `SettledRun` — the scope, and the entries of
kept games a download now holds — so the media follow (`SyncedScope.keeps` takes the platform slug, so the media service
judges its own record by the same rule).

### The seams in downloads, uninstall, adoption and the game page

`EsdeStandInGuard` (`services/protocols/cross_service.py`) is what the other services ask: `do_stand_in_at(path)`
(synchronous, never follows a link), `clear_for_download(rom_id)` and `restore(rom_id)`. Each consumer holds it in its
config with a default of `None`, meaning nothing is a stand-in: `DownloadServiceConfig`, `RomAdoptionServiceConfig`
(handed on to `CandidateSearchConfig` and `AdoptionRenamerConfig`), `RomRemovalServiceConfig` and
`GameDetailServiceConfig`.

- **Download.** `_begin_download` clears the game's own stand-in right after it takes the in-progress claim, so the
  gate, the unpack and ES-DE's collapse never see it. A start that starts nothing gives the claim back and then puts
  back what it cleared; a failed or cancelled download puts it back in its `finally`, after the claim is released, and
  so does cancelling a paused download. `restore` declines where the game has an install row.
- **The race with downloads.** A restore and a pass's placement read the downloads in flight (`ActiveDownloadRomIdsFn`,
  through a `LateBinding`, since the stand-in service is built before the download service) under the lock
  `clear_for_download` takes. A download claims its game before it clears it, so a placement that wins the lock first is
  removed by the clear that follows, and one that comes after sees the game in flight. A game being downloaded keeps its
  record, so a failed download puts back the stand-in last decided for it.
- **Adoption and the game page.** The candidate search drops every stand-in from its name matches, so a sibling's is no
  namesake and the game's own folder stand-in is no candidate. The game page's target probe asks about the joined path
  before `safe_join` resolves it, since a stand-in resolves to its stub; the missing-install probe treats a stand-in at
  a recorded path as gone. `AdoptionRenamer.target_taken` lets the adopted game's own stand-in at the canonical name
  pass, and the adoption clears it before the carry and puts it back if it records nothing; the carry's own re-check
  stays strict, so a stand-in that could not be cleared refuses with `target_taken`. A stand-in at a game's own target
  is never adopted (`unexpected_content_kind`).
- **Uninstall.** `remove_rom`, `uninstall_all_roms` (each game it removed) and `forget_download` put the stand-in back
  once the install record is dropped. `remove_rom_unchecked`, which the sibling supersede reaches, does not — the new
  version is the game ES-DE lists once it installs — and nor does the removed-game cleanup's `delete_rom_files`.
- **Leaving ES-DE.** On a surface that writes no ES-DE the stand-in service ends the run in flight, so a unit decided
  across the switch places nothing, and removes every stand-in, its stub and its record.

No stub reaches anything but ES-DE: a stand-in is never an install, so no save question, adoption, verification or
launch of Tender's sees one.

## ES-DE's folders

ES-DE as RetroDECK ships it reads its gamelists, event scripts and media below `<rd_home>/ES-DE/`.
`RetroDeckFolders.esde_folders()` (`adapters/retrodeck_folders.py`) answers the three as `EsdeFolders`
(`domain/esde_folders.py`), joined onto RetroDECK's home, each symlink-resolved; nothing is created. It is refused as a
download is refused — RetroDECK not detected or switched off, the roots unanswerable, the home missing — and wherever
the home is one the resolver only assumed (`domain/source_roots.py`). This is the **one exception** to "every RetroDECK
folder is the resolver's answer" (the invariant register): the vendored resolver answers the home and none of the three
folders, and `_vendor/` is never edited (D13). A `downloaded_media` folder moved with RetroDECK's Configurator is not
followed.

## Media

Every image RomM holds for a game the sync lists in ES-DE is fetched, kept under the cache root and linked into
`<downloaded_media>/<system>/<folder>/<stem><ext>`; the video and the manual are fetched when ES-DE selects the game.

**The mapping** (`domain/esde_media.py`) follows RomM's own ES-DE exporter (`_collect_assets`,
backend/utils/gamelist_exporter.py): `covers` is `path_cover_large`, else `path_cover_small`; `marquees` is the wheel,
`logo_path`, else the gamelist block's `marquee_path` — ScreenScraper's `marquee_path` is a screen marquee and never
used; `screenshots` is the first `merged_screenshots` entry under the ROM's own `screenshots/` folder, else the first;
`titlescreens`, `3dboxes`, `backcovers`, `physicalmedia`, `fanart` and `miximages` are their `*_path`, read from
`ss_metadata`, then `gamelist_metadata`, then `launchbox_metadata`; `videos` and `manuals` are `path_video` and
`path_manual`. A relative path lies under `/assets/romm/resources/`; a URL, a scheme, `..` and every `*_url` are never
answered. `custom` is never written. Which `scan.media` value RomM needs before it stores each type is
`domain/esde_scan_media.py`'s `SCAN_MEDIA_FOR_FOLDER`.

**Names.** `<stem>` is ES-DE's `getStem` of the entry ES-DE lists the game as — its stand-in, or the install's top-level
file or folder (`install_entry`) — so a directory interpreted as a file keeps its whole name
(`Final Fantasy VII.m3u.png`). ES-DE takes the first extension of its lookup order that exists (`sImageExtensions`,
`sVideoExtensions`, es-app/src/FileData.h; `FileData::getManualPath`), so the format is sniffed from the bytes
(`sniff_extension`: png, jpg, webp, mp4 by its `ftyp` brand with HEIF and AVIF excluded, mkv and webm by an EBML walk to
the DocType, pdf) and bytes ES-DE does not read are dropped: Tender carries no image converter.

**The fetch** is `RommMediaReader.download_media(server_path, dest, *, etag, last_modified)`, which `RommApiAdapter`
answers over `RommHttpAdapter.download_conditional`, the cover's own byte-stream fetch — so no new request site and no
new 404 translation. The path is encoded as a cover's is (`quote(path, safe="/:?=&@")`), the configured custom headers
and the bearer go with it, and a stored validator makes it conditional. Its 404 is the static mount's and says nothing
about the ROM, so the method answers `None` rather than let `RommNotFoundError` out, and no 4xx marks RomM unreachable.

**Fingerprints** (`domain/esde_media_record.py`). A folder is fetched again only where its fingerprint — the server path
and the ROM's `updated_at` — moved, or its copy is gone; a kept copy of the same file (the same path, `?ts=` aside) is
revalidated with its ETag and Last-Modified rather than fetched whole. So an unchanged library costs no media request. A
404 leaves the slot empty and is not asked again until the fingerprint moves; any other failure leaves the slot as it
was and is asked again next time; a folder RomM no longer has is unlinked and its copy forgotten.

**Ownership** (D11, `adapters/esde_media_files.py`, `EsdeMediaStore`). Copies live at
`<cache>/esde/media/<rom_id>/<folder><ext>`, and `<cache>/esde/on-demand/<rom_id>/<folder><ext>` for the two folders
fetched on selection. A link is Tender's exactly when its target lies below `<cache>/esde/`. Before linking, every name
ES-DE reads for that stem and folder is looked at; if any is not Tender's — a file, another program's link — nothing is
linked for the slot and Tender's own links there go. A free path takes `os.symlink` straight there; only a link of
Tender's, re-read with `lstat`/`readlink` right before, is replaced (a temporary link and `os.replace`), and once the
new link stands Tender's links under the other extensions go. A broken link of Tender's is replaced.

**The record** is `<data>/esde/media.json` (`adapters/esde_media_index.py`; atomic; unreadable reads as empty and is
logged): per game the platform, system, entry stem and `updated_at`, and per folder the server path, fingerprint, ETag,
Last-Modified, extension and link path; an on-demand folder also keeps its last selection and size. The service reads it
once and keeps it in memory, writing it when the queue runs dry and after a selection, a run's end, a switch and at
shutdown: one rewrite per game would make a first fill of a library quadratic.

**The service** (`services/esde/media.py`, `EsdeMediaService`). `queue(entries, roms)` answers at once and brings each
game up to date in the background, three fetches at a time (`BackgroundTasks.spawn`); a game already queued is queued
once, as the newer entry says, and a game queued without its ROM is only moved to its entry's stem. `settle(scope)`
removes the links and copies of every game the scope no longer keeps and drops their queued work. A fetch begun before
its game was removed, or before ES-DE was left, links nothing. Leaving ES-DE removes every link of Tender's in the media
tree, recorded or found by walking it, and every copy. `Application.shutdown` stops it after the library sync.

**On selection** (D4). `fetch_on_selection(rom_id)` (`EsdeOnDemandMedia`) answers at once, coalesces a selection already
under way, fetches the video and the manual from the recorded server path only while the setting writes ES-DE, stamps
the selection with the injected clock, and evicts over `ON_DEMAND_CAP_BYTES` (8 GiB) the copies of the games selected
longest ago — an entry larger than the cap first — each link before its copy. ES-DE fires `game-select` after it has
looked the game's media up, so what this fetches shows from the next selection.

## Gamelists

Tender's seven fields — `name`, `desc`, `releasedate`, `developer`, `publisher`, `genre`, `players` — go into
`<gamelists>/<system>/gamelist.xml` for every game the sync lists in ES-DE (`domain/esde_gamelist_fields.py`, through
`build_rom_metadata`; ES-DE's `YYYYMMDDT000000` date with a four-digit year, ES-DE's default `19700101` left out). Never
`altemulator`, never a media tag: ES-DE matches media by file name.

**The entry's `<path>`** is `./` and the name ES-DE lists the game as (`domain/esde_gamelist_record.py`,
`gamelist_entry_path`), which is how ES-DE writes it: `GamelistFileParser::addFileDataNode` sets
`createRelativePath(file->getPath(), system->getStartPath())` (FileSystemUtil.cpp), and a directory interpreted as a
file is a game whose path is the directory (`SystemData::populateFolder`). A name ES-DE would read back as no one entry
(a slash or a backslash, `.`, `..`) or holding a character XML cannot carry gets no entry, nor does a system that names
no one folder.

**The merge** (`domain/esde_gamelist.py`) is a splice over the file's text, never a parse and re-serialisation, so every
byte outside Tender's fields in Tender's entries stays; ElementTree only validates. In Tender's entry a field that is
absent or empty is written, one still holding Tender's last value is replaced (removed where the new value is empty),
and any other value is kept, its record keeping Tender's last value. Missing entries are appended after the last element
in the file's own newline and indentation. What it reads from ES-DE's source (GamelistFileParser.cpp, MetaData.cpp,
FileSystemUtil.cpp): `<alternativeEmulator>` sits beside `<gameList>` at document level, so validation wraps the body
and refuses any DOCTYPE; a path matches the way ES-DE resolves it, and only a `./`-relative one names a file in the
system folder; of two `<game>` entries for one file the last is Tender's, because `parseGamelist` replaces metadata
entry by entry; a `<folder>` is never Tender's; only a field's first child is judged (`node.child(key)`); a
whitespace-only value counts as empty; a field holding markup is never Tender's. A file that is not a gamelist
(`UnreadableGamelist`) is left as it is with a warning and its entries keep waiting.

**Pending and written.** During a run `EsdeGamelistService.collect` leaves each unit's fields pending per system; a unit
skipped as unchanged adds nothing. `settle`, after the media at the run's end, asks `EsdeRunningFn` whether an ES-DE
runs and, where none does, writes every system with entries pending: the file is read (none is fine), merged against
what Tender last wrote, written only when the merge changed it, and the record takes the merge's `written`. Where ES-DE
runs, everything stays pending for `write_pending`, the call ES-DE's `startup` event makes, which writes regardless of
the check (`EsdePendingGamelists`). The record is `<data>/esde/gamelists.json` (`adapters/esde_gamelist_index.py`;
atomic; validated on read so a pending path or field the merge would refuse never comes back): per system `pending`
(path to fields) and `written` (exactly what the merge answered). It is read once and written whenever it changes, under
one lock, so pending outlives a restart and a run's end and ES-DE's start never interleave.

**Why never while ES-DE runs** (D16). ES-DE rewrites a gamelist while it runs, not only on exit: under its default
SaveGamelistsMode "always" every save point — a launch's playcount among them, `FileData::launchGame` — runs
`updateGamelist`, which re-reads the file and puts back each entry it changed from what it read at its start, so a write
of Tender's made meanwhile is lost for those entries; under "on exit" the same happens in `~SystemData`. The `startup`
event fires before ES-DE reads any gamelist and holds ES-DE until its scripts finish.

**The running check** is `EsdeProcessAdapter` (`adapters/esde_process.py`): `/proc/<pid>/comm`, falling back to the
basename of the first `cmdline` element, matched against `es-de` in any case, as `renderer_rss` reads the table.
RetroDECK's sandbox puts ES-DE's processes in the host's table all the same. A table that cannot be listed answers that
ES-DE runs.

**The files.** `EsdeGamelistFilesAdapter` (`EsdeGamelistStore`) reads the text with no newline translation and the byte
order mark kept, and writes it whole through a temporary file in the file's own folder, fsynced and renamed over it,
keeping its mode and creating the system folder; a linked gamelist is written where the link points.

**Leaving ES-DE** drops what is pending and leaves every gamelist as it is, Tender's fields included: removing them
would need ES-DE closed, and the merge rule leaves an entry Tender no longer owns alone. The `written` record is kept,
so should ES-DE be chosen again a field still holding Tender's value is updated and one changed since stays the user's.

## The hook bridge

ES-DE runs inside RetroDECK's flatpak, with `--share=network` and `--filesystem=host` (`net.retrodeck.retrodeck.yml`):
it reaches the host's loopback and reads anything under the home folder, but not `/run`, where the port file lies. Its
event scripts call the backend with `curl`, over a third entry point of the host.

### The route and its admission

`GET /hooks/esde/<name>`, arguments in the query, no body; any other method is 405. `host.access.check_access` judges it
as it judges the files and the upgrade — Host, then Origin, then token, with the same 421/403/401
(`.claude/rules/host.md`, rule 3). Which token decides follows from the path alone (`is_hook_path`, read by the
admission and the routing alike): a path under `/hooks/` is judged by the **hook token**, offered in the
`X-Tender-Hook-Token` header, and every other path by the admission token in the query. Neither admits the other's
routes, and a hook refusal is never flagged `wrong_token`, so the stranded-panel close codes stay the upgrade's alone.
Both tokens are compared as bytes, so an offer holding a non-ASCII character is refused rather than raising.

**The answer** (`host/hooks.py`). The host looks the name up in the mapping of hook name to callable it is handed on
`BackendBuild.esde_hooks`, typed with standard-library types only, and calls it with the decoded query
(`hook_arguments`: one value per name, blank values kept, percent escapes decoded as `os.fsdecode` decodes a file name,
so a non-UTF-8 ROM path round-trips). The answer goes back as `application/json` with 200, a refusal in the endpoints'
`{success, reason, message}` shape included. A path naming no hook is a bare 404, an unreadable query (a name given
twice, a raw non-ASCII byte) a bare 400, no mapping wired a bare 503. A raising handler, an answer that cannot be
encoded and one over the dispatcher's 12 MiB cap are each a 500 carrying the transport's
`{type, reason, message, traceback?}`, for that request alone — the two failure shapes stay apart here as on the
WebSocket.

`main.py` builds the mapping from `EsdeHooks`, a class beside `Endpoints` rather than on it, since every public method
of `Endpoints` is reachable over the WebSocket. It maps `startup`, `prepare`, `finish` and `select` onto the
`EsdeHookHandler` (`services/protocols/infra.py`), wrapped by `_translating_refusals`, so a `Refused`, `DomainRefused`
or `RommApiError` answers the failure shape and anything else — a missing argument included — reaches the host as a 500.

### The hook token and its files

The hook token is minted in `main.run()` at every start, beside the admission token and before logging is configured
(D10); `configure_logging` and `RedactingFormatter` take both tokens to keep out of the log file, and stderr keeps its
plain formatter. Once the port is bound, beside the port file, `host.hooks.HookFiles` writes `<data>/esde-hooks/` (0700)
with `port` and `header` (each 0600, through a temporary renamed over the old file, the header first, so a script that
finds this start's port finds this start's token). `header` holds the one line `X-Tender-Hook-Token: <token>` that
`curl -H @file` sends, so the token is in no request line — which an unreadable request is logged with, by its length
only — and on no command line. Both files go at shutdown with the port file, so finding them means the port answers. A
place they cannot be written costs the hooks only: it is logged and the backend runs on. Where a script finds the
backend (`EsdeHookAddress`: the two files and the route prefix) is decided in `main.esde_hook_address` from the host's
constants and passed down through `build_application` and `WiringConfig`, so nothing below the entry point imports
`host`; `check.py` builds it the same way.

### The scripts

`domain/esde_hook_scripts.py` writes one POSIX `sh` script per event, `<scripts>/<event>/tender-romm.sh`, whose second
line is Tender's marker. ES-DE runs every entry of `<scripts>/<event>/` on the event, in sorted order, as
`"<script>" "<arg1>" … "<arg4>"` through `/bin/sh -c`, and ignores its exit code (`Scripting::fireEvent`,
es-core/src/Scripting.cpp). Each script reads the port file, admits itself with `curl -H @<header file>`, sends the path
with `--data-urlencode`, passes `--noproxy '*'` so a proxy in the environment never takes a loopback call, discards
curl's output and always exits 0 — also with the hook files missing, a port file naming no port, no `curl`, `curl`
failing, or no path (game-select's fast-scroll call).

| ES-DE event   | Hook      | Path sent            | `--max-time` | Waits?                                    |
| ------------- | --------- | -------------------- | ------------ | ----------------------------------------- |
| `startup`     | `startup` | none                 | 20 s         | yes; ES-DE is held until it ends          |
| `game-start`  | `prepare` | escaped, `escaped=1` | 4 h          | yes; ES-DE holds the launch with no bound |
| `game-end`    | `finish`  | escaped, `escaped=1` | 1 h          | no; called in the background              |
| `game-select` | `select`  | plain                | 10 s         | no; called in the background              |

`startup`'s 20 s outlasts the backend's own 15 s, so the backend answers first; `prepare`'s bound is hours because it
downloads.

**ES-DE's argument escaping** (`domain/esde_hook_path.py`). ES-DE passes `game-start` and `game-end` the path as
`Utils::FileSystem::getEscapedPath` makes it (es-core/src/utils/FileSystemUtil.cpp): every backslash turned into a
slash, then a backslash before each space and each of `\ ' " ! $ ^ & * ( ) { } [ ] ? ; < >`. `fireEvent` wraps that in
double quotes for `system()`, and inside double quotes the shell takes away only the backslash before `"`, `$`, a
backtick, a backslash or a newline, so the script still receives the backslash before every other escaped character;
`unescape_esde_path` takes exactly those away and leaves any other backslash. A backtick is not escaped at all, so the
shell runs what lies between two of them, and a backslash in a name has become a slash: such paths never reach a script
intact. `game-select` is passed the plain path inside the same double quotes, so there `$` and `"` are taken by the
shell too. A path ES-DE names is a link's own path, never its target, and for a directory interpreted as a file the file
of that name inside it (`FileData::launchGame`, es-app/src/FileData.cpp).

**Which game a path names.** A path is Tender's when the stand-in guard names a game for it (`do_stand_in_at`, which
also answers for the file inside a stand-in folder), or when — every path spelled with its symlinks resolved
(`ResolvedPathFn`) — it is an install's recorded file, its `rom_dir`, or a file directly in that folder, a recorded file
matching first (`installed_game_at`). Anything else is refused with `not_a_tender_game` and nothing is done. The install
row is read through the Unit of Work rather than `get_installed_rom`, because the folder match needs `rom_dir`.

**Installing them.** `adapters/esde_hook_scripts.py` (`EsdeHookScriptStore`) writes a script executable (0755) through a
temporary renamed over the old one, and writes or removes only where nothing is, or where a regular file, read without
following a link, begins with the marker; anything else at the name, a link included, is left. `EsdeHookScriptService`
installs them at the backend's start when the surface writes ES-DE (the start-up step `install_esde_hook_scripts`), and
on each surface stored: installed for `esde` and `both`, Tender's removed for `steam`. A folder that is refused, and a
script that cannot be written or removed, is logged and the rest go ahead.

### What each hook does

`EsdeHookService` (`services/esde/hooks.py`) reaches the other services through narrow Protocols — `LaunchDownloader`,
`LaunchSaveSync`, `SessionStartRecorder`, `SessionFinalizer`, `EsdeOnDemandMedia`, `EsdePendingGamelists` — and **checks
no conflict rule of its own**: every use case it calls checks its own at its entry, and a refusal one raises is said and
answered. Every hook records the last call (`last_call`: hook and ISO time) in memory, refused calls included.

- **`prepare`.** For a game with no install row: `start_download`, then wait for its task with `asyncio.wait`, unbounded
  and never cancelling it (D6), then read the install row; a download already under way (`already_downloading`) is
  waited for. A gate question (`target_occupied`, `adoption_candidates`, `unusable_namesake`, `candidate_vanished`) is
  said as "open Tender" and raised; any other refusal, RomM error or partial failure is said and raised; a download that
  ends without an install is `download_failed`. One that landed under another name than ES-DE lists (D5) says to restart
  ES-DE and stops there. Then, for an installed game: `record_session_start` (a failure is logged), the slot
  confirmation the panel's Steam launch does silently (`is_save_tracking_configured`, `get_save_setup_info`, then
  `confirm_slot_choice` with the slot `domain/save_slot_setup.py` picks — nothing where the user must choose or the
  server's slots were not read), and `pre_launch_sync`. The slot step and the pull are each waited for at most
  `SAVE_WAIT_SECONDS` (15 s, the panel's own bound); one that outlasts it is left to finish, held by the service's
  `BackgroundTasks`, never cancelled mid-wait, since stopping a save sync partway could leave a save and its record
  apart; `shutdown` cancels what is still running. What the pull did is said whatever it was. The hook then answers
  success, so a game that is there to start always is.
- **`finish`.** `SessionFinalizer.finalize` for an installed game — the session's end and the post-exit save push — and
  its result or refusal goes out in an `esde_session` event. A stand-in never downloaded has no session and refuses
  `not_installed`.
- **`select`.** Hands the game to `EsdeMediaService.fetch_on_selection`, which answers at once.
- **`startup`.** Awaits `EsdeGamelistService.write_pending` under a `STARTUP_WAIT_SECONDS` (15 s) `asyncio.timeout`,
  cancelled past it, since ES-DE reads its gamelists as soon as the hook answers; `finished` says whether it was in
  time.

**Events and toasts** (D8). `esde_launch` carries a stage — `downloading`, `not_downloaded` with a message,
`listed_elsewhere`, `saves` with the pull in the shape `finalize_game_session` reports its sync in — and the game's id
and name; `esde_session` carries the finalize result or its refusal. The panel's `frontend/src/utils/esdeToasts.ts`,
registered beside the other global listeners in `index.tsx`, toasts them in the session manager's words
(`saveSyncToastBody`) and refreshes what shows the game's saves, as a game's end in Steam does. ES-DE gives a script no
way to draw on its screen, so Steam's notifications are the only place a start from ES-DE is reported. The hook service
shuts down with the application, before the downloads.

## The status read

`get_esde_status` (`Endpoints`) delegates to `EsdeStatusService` (`services/esde/status.py`) and answers:

- `surface`;
- `scripts`: `{known: true, folder, installed, expected}` (`EsdeHookScriptService.read_installed`, over the store's
  `holds`, the marker test, a link at the name not counted), or `{known: false, reason, message}` with the
  `FolderRefused` of `esde_folders()`;
- `last_call`: `{hook, at}`, `null` before ES-DE's first call since the backend started;
- `media_not_collected`: `{established: true, folders}` or `{established: false, message}`. What RomM collects is
  `RommApiAdapter.get_scan_media`, behind `RommServerConfigReader`: `GET /api/config`'s `SCAN_MEDIA`, in one short
  attempt through `request_once`, so it goes out through `_urlopen` as a RomM-origin request; an answer without a list
  of names raises `RommApiError`, never an empty tuple, and any `RommApiError` answers not established with
  `classify_error`'s message. `folders_not_collected` turns it into ES-DE's folders: `covers` never, since RomM always
  stores the cover, and `videos` where neither `video` nor `video_normalized` is listed.

The settings section (`frontend/src/bigpicture/settings/EsdeSection.tsx`, its state in `useEsdeSettings.tsx`) reads it
each time it is opened; what it shows is [QAM Panel, Settings](qam-panel.md#settings).

## Composition

`bootstrap/adapters.py` builds the file adapters from `AppDirectories` (`cache_dir`, `data_dir`) and the process probe;
`bootstrap/services.py` builds the stand-in, media and gamelist services and the pass before the library sync and the
downloads, and the script, hook and status services after them, and hands the pass to the sync and to the settings.
`services.esde` is one entry of the service-independence contract. `check_uow_seam_nesting.py` lists `download_media`,
`kept_size`, `forget_game`, `unlink_all`, `esde_folders`, `read_gamelist` and `write_gamelist` among its file-I/O seams.

`Application.shutdown` stops the media service after the library sync, and the hook service after the removed-game
cleanup and before the downloads ([Backend Architecture](backend-architecture.md#the-host-backendhost)).

## What the tests can and cannot see

The contract tests under `tests/contract/test_esde_*.py` run real ES-DE syncs through the real wiring with RomM faked:
stand-ins, media, gamelists, hooks, the status read and the surface switch. ES-DE's escaping and command line are ported
into `tests/fakes/esde_events.py` and run through the real `/bin/sh -c`, and the scripts run under `/bin/sh` with a stub
`curl` and once through the real `curl` against a loopback server. What only the device can show is listed in the spec's
Phase 0: that ES-DE in RetroDECK's flatpak reaches the loopback port and reads the hook files, holds a launch for
`game-start`, lists a link to an empty stub and a stub folder, shows linked media, and keeps a gamelist written from
`startup`.
