# ES-DE Mode

Tender normally puts your RomM library into Steam as shortcuts. **ES-DE mode** puts it into the ES-DE that RetroDECK
ships instead: every game of the platforms you sync is listed there, with RomM's description, dates, companies, genres
and player count, and the box art, screenshots and other images RomM holds for it — whether the game is downloaded or
not. Starting a game that is not downloaded yet downloads it first, and saves sync with RomM around every start, as they
do for a game started from Steam.

In ES-DE mode Tender creates no Steam shortcuts. The Quick Access panel stays where your settings, downloads, BIOS files
and saves are managed, and it is where Tender tells you what it did when ES-DE started a game.

Only RetroDECK's own ES-DE is supported, in its current releases. An ES-DE installed on its own, or another frontend, is
not.

## Turning it on

Open **Settings › ES-DE** and set **Library surface**:

| Choice              | What a library sync does                                                                |
| ------------------- | --------------------------------------------------------------------------------------- |
| **Steam shortcuts** | Creates Steam shortcuts, as Tender always has. The default                              |
| **ES-DE**           | Lists the library in ES-DE and creates no Steam shortcuts                               |
| **Both**            | Creates Steam shortcuts and lists the library in ES-DE, so each game is in both of them |

The choice takes effect from the next library sync. It cannot be changed while a library sync is running: the setting
goes back to what it was and the section says why. Wait for the sync to finish, or cancel it, and choose again.

Choosing **ES-DE** or **Both** after **Steam shortcuts** makes the next sync read every platform from RomM in full, even
the ones that have not changed since the last sync, so that every game gets its images and its details in ES-DE.

Under **ES-DE** the Sync page shows no preview: a preview lists the changes to your Steam shortcuts, and there are none
to make, so the sync button starts the sync straight away. Under **Both** the preview works as before.

### Removing your Steam shortcuts

When you switch from **Steam shortcuts** or **Both** to **ES-DE**, Tender asks whether to remove the shortcuts it made
in Steam. **Remove shortcuts** takes them and Tender's Steam collections out of Steam, as **Remove all shortcuts** on
the Data Management page does; your downloads, saves and playtime stay. **Keep them** leaves them as they are, and you
can remove them from Data Management later.

### Turning ES-DE's scripts on

Starting a game that is not downloaded, syncing its saves, and fetching videos and manuals all work through ES-DE's
**custom event scripts**, which Tender writes into ES-DE's scripts folder while the library surface is **ES-DE** or
**Both**. ES-DE runs them only once you turn them on, which is a step in ES-DE itself:

1. In ES-DE, open **Main menu › Other settings**.
2. Turn on **Enable custom event scripts**.
3. Turn on **Enable browsing custom events** as well. Without it, games still download and their saves still sync, but
   ES-DE never fetches videos or manuals.
4. After Tender's first sync in ES-DE mode, restart ES-DE so that it lists the games Tender placed.

**Settings › ES-DE** shows whether Tender's scripts are in place (**ES-DE event scripts**), the folder they are in, and
when ES-DE last called Tender (**Last call from ES-DE**). **Never** there means ES-DE has not called since Tender
started: the scripts are not turned on yet, or ES-DE has not been started.

## A game that is not downloaded

A game you have not downloaded is listed in ES-DE like any other, under its system, with its details and images. What
ES-DE finds in the ROM folder for it is a **stand-in**: a link Tender placed at exactly the place the game's download
will go, pointing at an empty file of Tender's. A game with several files — a multi-disc game, for instance — gets a
folder named like the playlist the download will write, which ES-DE lists as one game.

Tender places a stand-in for every game it would give a Steam shortcut: one version of each game, the one Tender picks
for the shortcut, by your preferred region. Another version of a game appears in ES-DE once it is downloaded. Tender
never places a stand-in where something of yours already lies at that path, and it never replaces or removes anything
but its own stand-ins.

### Starting it

When you start a game that is not downloaded, Tender downloads it before ES-DE starts the emulator. ES-DE waits for as
long as the download takes and shows nothing while it does, so Steam's notifications say what is happening:

1. **Downloading _game_…** as the download starts.
2. Once the game is there, Tender pulls the game's saves from RomM, as Steam does before a game starts. It waits for at
   most 15 seconds; a sync that takes longer carries on while the game runs. A notification says what moved, or what
   went wrong: RomM offline, a conflict waiting for you, saves that did not sync.
3. ES-DE starts the game.
4. When you leave the game, Tender pushes its saves to RomM and records the session's playtime, and a notification says
   what moved.

A downloaded game skips step 1. Where the download cannot be done, the notification reads **Couldn't download _game_**
with the reason, and the emulator ES-DE then starts finds only the empty stand-in. Where the download needs a decision —
a file of yours already lies where it would go, or a file of yours may be the game already — nothing is downloaded and
the reason says to open Tender: that question is asked on the game's page in Steam (see Known limits).

Uninstalling a game, **Uninstall all ROM files** and **Forget this download** put its stand-in back, so the game stays
listed in ES-DE.

## Media

Tender links every image type RomM holds for a game into ES-DE's media folder. RomM is the only source; SteamGridDB is
not used in ES-DE mode.

| ES-DE folder                                  | From RomM                                | When                        |
| --------------------------------------------- | ---------------------------------------- | --------------------------- |
| Covers                                        | the cover                                | at sync                     |
| Marquees                                      | the wheel logo                           | at sync                     |
| Screenshots                                   | the first gameplay screenshot            | at sync                     |
| Title screens, 3D boxes, back covers, fan art | the image of that kind                   | at sync                     |
| Physical media                                | the cartridge, disc or tape              | at sync                     |
| Miximages                                     | RomM's composite image, where it has one | at sync                     |
| Videos                                        | the gameplay video                       | when ES-DE selects the game |
| Manuals                                       | the manual, PDF only                     | when ES-DE selects the game |

RomM stores a type only when its server is set to collect it. Out of the box RomM collects covers, screenshots and
manuals; the other types need the `scan.media` list in RomM's `config.yml` to name them — `logo`, `title_screen`,
`box3d`, `box2d_back`, `physical`, `fanart`, `miximage` and `video` — and an **Update metadata** scan of the library in
RomM afterwards, so that the newly enabled types are downloaded for games already matched. **Settings › ES-DE** names
the types your RomM does not collect under **Media RomM does not collect**.

The images are fetched in the background while the sync runs and after it, a few at a time, so a first sync of a large
library keeps fetching for a while after its progress bar has finished. A sync fetches an image only when RomM's file or
the game changed since the last fetch, so a sync of an unchanged library downloads no image at all. ES-DE reads a game's
images when the game is selected: a new image shows the next time you select the game, and a replaced one may keep
showing the old picture until ES-DE restarts.

A game without a miximage from RomM can still get one from ES-DE's own offline miximage generator, which builds it from
the images Tender linked.

**Your own media are never touched.** Where ES-DE's media folder already holds an image of a game's kind that Tender did
not put there — one ES-DE's scraper downloaded, or one you copied in — Tender leaves it alone and links nothing for that
game and kind.

### Videos and manuals

Videos and manuals stay on RomM until ES-DE selects a game: when the cursor stops on a game, Tender fetches its video
and manual. ES-DE has already looked for them by then, so they show from the next time you select the game. This needs
**Enable browsing custom events** (above).

Tender keeps at most 8 GiB of them. Over that, the videos and manuals of the games selected longest ago are removed
first, and fetched again the next time those games are selected.

## Game details in ES-DE

Tender writes seven fields of each game into ES-DE's gamelist for the game's system: its name, description, release
date, developer, publisher, genre and number of players, from what RomM knows about the game.

- **It changes only its own fields in its own games' entries.** Every other entry, and every other field — your
  favourites, playcount, ratings, a field ES-DE's scraper filled — stays exactly as it was. A field you edited in ES-DE
  stays your edit on every later sync.
- **It writes nothing while ES-DE is running**, because ES-DE writes the same file and would overwrite the change. A
  sync that ends while ES-DE runs keeps the changes waiting, and they are written the next time ES-DE starts, before it
  reads its gamelists. That needs ES-DE's custom event scripts (above); restart ES-DE to have them written.

## Turning it off

Setting **Library surface** back to **Steam shortcuts** removes what Tender put into ES-DE, and nothing else:

- every stand-in, so the games you have not downloaded disappear from ES-DE;
- every media link Tender made, and its own copies of the images, videos and manuals;
- its event scripts in ES-DE's scripts folder.

What stays:

- your downloaded games, in RetroDECK's ROM folders, which ES-DE keeps listing;
- the gamelists, **with Tender's fields still in them**. Taking them out would need ES-DE closed and risks your own
  entries, so Tender leaves the files as they are; changes still waiting for ES-DE's next start are dropped;
- anything of yours in ES-DE's folders.

The next sync creates Steam shortcuts for the library again. Switching between **ES-DE** and **Both** removes nothing.

## Known limits

- **A zip that may hold discs can land under another name than its stand-in.** Tender predicts where a download will
  land from what RomM lists about the game's file. For a single zip RomM lists nothing inside, the prediction is the zip
  itself, and if the zip turns out to hold disc images that Tender unpacks, the game lands as a folder of another name.
  That one start fails on the empty stand-in, and a notification reads **Restart ES-DE to start _game_**; after the
  restart ES-DE lists the real game, and its images follow it at the next sync.
- **A media folder moved with RetroDECK's Configurator is not followed.** Tender writes into
  `<RetroDECK home>/ES-DE/downloaded_media`, `…/ES-DE/gamelists` and `…/ES-DE/scripts` below the RetroDECK home its
  settings name. Where ES-DE's media folder has been moved elsewhere, ES-DE does not see the images Tender links.
- **Saves are synced where Tender's own choice of emulator keeps them.** Tender syncs a game's saves at the place the
  emulator Tender picks for the game writes them — the one it would start from Steam. A game you start in ES-DE with
  another emulator, chosen in ES-DE, may keep its saves somewhere else, and Tender then syncs the wrong place.
- **Some file names do not reach Tender intact.** ES-DE hands its scripts a game's path through the shell, which changes
  a path holding a backtick or a backslash before Tender sees it, so Tender cannot tell which game such a path names:
  the game is not downloaded on start and its saves are not synced. For videos and manuals the same is true of a path
  holding `$` or `"` as well.
- **Under ES-DE alone, the panel has no place to download a game.** A download that needs a decision, and a download of
  another version of a game, start from the game's page in Steam, which a game without a shortcut does not have. Choose
  **Both** while you need them.
- **Download progress is shown only in Steam's notifications.** ES-DE gives a script no way to draw on its screen.

Where Tender keeps what it records for ES-DE mode is on [Where Your Data Lives](where-your-data-lives.md).
