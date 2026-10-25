---
status: accepted
decided: 2026-10-25
updated: 2026-10-25
amends: [0042]
---

# ES-DE library mode: Tender writes stand-ins, media links, gamelists and event scripts into RetroDECK's ES-DE

Recorded for the ES-DE library mode epic of the `ProphetWild/romm-tender` fork, whose spec is
[docs/specs/esde-library.md](../specs/esde-library.md); it carries that spec's D7, and the decisions D5, D10, D11, D13
and D16 it rests on. It is offered upstream only after the device pass.

## Context

The goal is the whole RomM library playable from the ES-DE RetroDECK ships, on a Steam Deck, with no Steam shortcuts:
every game listed whether downloaded or not, with RomM's metadata and media, a game not downloaded downloaded when it is
started, and saves synced around every start.

[ADR-0042](0042-tender-takes-emulator-answers-starts-emulators-and-keeps-its-own-store.md) sets the opposite course for
frontends. Tender has no reader of frontend files (decision 1); downloaded games live in Tender's own library and reach
a frontend only as links to files Tender records (decision 4); Tender creates no folder in a source (decision 4) and
writes no emulator's configuration but one (decision 8). That ADR is accepted but not built: 0.33.0 still downloads
straight into RetroDECK's ROM folders, and its library waits on resolver answers that do not exist yet.

ES-DE lists only what lies in a system's folder, reads a game's metadata only from its own `gamelist.xml`, reads media
only from its own `downloaded_media` tree by file name, and tells nothing outside itself that a game was started except
through its custom event scripts (`Scripting::fireEvent`, es-core/src/Scripting.cpp). None of the goal is reachable
without writing into those places, and the resolver answers none of ES-DE's three folders.

## Decision

**Under the library surface `esde` or `both`, Tender writes four kinds of thing into RetroDECK's ES-DE, each recognised
as its own by what it is and nothing else of the user's ever replaced or removed:**

1. **Stand-ins** in RetroDECK's ROM folders: for a game not downloaded, an absolute symlink at exactly the path its
   download will take, pointing at an empty stub below `<cache>/esde/stand-ins/`; Tender's exactly when its target lies
   there (D5, D15).
2. **Media links** in `<rd_home>/ES-DE/downloaded_media/`: absolute symlinks to copies of RomM's files below
   `<cache>/esde/`; Tender's exactly when their target lies there, and no slot that already holds a file of anybody
   else's gets one (D11).
3. **Gamelist fields** in `<rd_home>/ES-DE/gamelists/<system>/gamelist.xml`: seven fields, merged by a splice that
   changes only Tender's own fields in Tender's own entries and keeps a field the user edited, written only while no
   ES-DE runs or from ES-DE's `startup` event (D16).
4. **Event scripts** in `<rd_home>/ES-DE/scripts/<event>/tender-romm.sh`, recognised by a marker on their second line,
   which call a hook route of the backend's own behind a token minted at every start and handed over in a file under the
   data root (D10).

ES-DE's three folders are joined onto RetroDECK's home, which the resolver answers, and only while that home was read
rather than assumed: the one exception to the rule that every RetroDECK folder is the resolver's answer, until emu-atlas
answers these folders (D13). Downloads keep landing in RetroDECK's ROM folders, as 0.33.0 does.

## Considered options

- **Wait for ADR-0042's library and frontend links.** Rejected for this fork: the library waits on resolver answers that
  do not exist yet, and a frontend link to a downloaded game still leaves a game that is not downloaded unlisted, its
  metadata and media unwritten and its start unseen.
- **Real files for media, copied into ES-DE's tree.** Rejected: a copy cannot be told from one ES-DE's scraper or the
  user left, so Tender could never replace or remove its own safely. A link with a target under the cache root can.
- **Rewrite the gamelist from a parse.** Rejected: a serialiser rewrites the whole file — indentation, quoting, entity
  spelling and the element ES-DE keeps beside `<gameList>` — that ES-DE, its scraper and the user also write.
- **A launch wrapper in place of `game-start`.** Rejected: ES-DE waits for its `game-start` scripts with no timeout
  (`FileData::launchGame`, es-app/src/FileData.cpp), so the download can run inside the hook and nothing of RetroDECK's
  launch is replaced (D6).
- **The hook token beside the port file under `/run`.** Rejected: RetroDECK's flatpak shares the home folder and the
  network but not `/run` (`net.retrodeck.retrodeck.yml`).

## Consequences

- Tender reads and writes a frontend's own files, which ADR-0042 decision 1 rules out, and creates folders in an
  emulator source — a system's gamelist folder, an event's scripts folder — which its decision 4 rules out. Each write
  is confined to what Tender can recognise as its own, and the invariant register carries the rules that confine it.
- A stand-in sits where a download will land, so the download gate, adoption's search, the game page's probes and
  uninstall see past it through one seam (`EsdeStandInGuard`); a wrong prediction (a zip whose members decide) costs one
  failed start and a toast.
- The host gains a third entry point and a second token, each admitting only its own routes; the token-never-a-header
  rule stays the admission token's.
- Leaving ES-DE removes the stand-ins, media links, copies and scripts, and leaves the gamelists as they are, Tender's
  fields in them: removing those needs ES-DE closed and touches the files the user's entries live in.
- ADR-0042's warning about different emulators writing saves to different places stands (D9): the save sync asks with
  Tender's own choice of emulator, not ES-DE's (D14).
- A `downloaded_media` folder moved with RetroDECK's Configurator is not followed until the resolver answers ES-DE's
  folders.
