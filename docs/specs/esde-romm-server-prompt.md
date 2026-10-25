# Prompt: prepare RomM on the Mac mini for Tender's ES-DE mode

Paste everything below the line into Claude Code on the Mac mini that runs RomM.

---

RomM runs in Docker on this Mac. I want it to collect every kind of game media that ES-DE (inside RetroDECK on my Steam
Deck) can show, videos included, so that Tender on the Deck can pull them from RomM. Work through the steps below in
order, and stop and ask me before anything you cannot undo.

Ground rules:

- Never print, log, paste into a file under version control, or repeat back any credential, password or token. When you
  need one, ask me to enter it myself (for example `read -rs ROMM_TOKEN && export ROMM_TOKEN`) and refer to it only by
  its variable name. When you list environment variables, list names only, never values.
- Before you change a file, copy it to `<name>.bak-<date>` beside it, and show me the diff after the change.
- Ask me before you restart or recreate a container, upgrade RomM, or start a scan.
- Look things up in the RomM version that is actually installed (its docs at docs.romm.app and its source on GitHub for
  that tag) rather than trusting the notes below; the notes are what was true for RomM 5.3.0 to 5.4.1.

1. **Find the deployment.** Find the RomM container (`docker ps`), the compose file that starts it, the host folder
   mounted at `/romm/config`, and the volume holding `/romm/resources`. Tell me RomM's version (the `SYSTEM.VERSION` in
   `GET /api/heartbeat`). It must be at least 5.3.0; 5.4.1 or later is better, because it adds LaunchBox box faces,
   per-disc physical media, replacement of stale media and `POST /api/tasks/scan`. If it is older than 5.4.1, propose an
   upgrade with a database backup first, and wait for my answer.

2. **Metadata providers.** Most media types come from ScreenScraper. Check which provider variables the container has
   (names only): `SCREENSCRAPER_USER` and `SCREENSCRAPER_PASSWORD` are required for this; `STEAMGRIDDB_API_KEY`,
   `IGDB_CLIENT_ID` / `IGDB_CLIENT_SECRET` and `LAUNCHBOX_API_ENABLED=true` are useful extras. If ScreenScraper is
   missing, ask me for the account and put it where the other variables live (the compose file's `environment:` or an
   `.env` file with mode 600). Environment variables are read once at start, so a change needs the container recreated
   (`docker compose up -d`); ask me first.

3. **Media types.** In `config.yml` (the host side of the `/romm/config` mount), set `scan.media` to exactly:

   ```yaml
   scan:
     media:
       - box2d
       - box2d_back
       - box3d
       - physical
       - miximage
       - screenshot
       - title_screen
       - logo
       - marquee
       - fanart
       - manual
       - video
   ```

   Keep every other key in the file as it is. Edit the file rather than using the web UI's Scan settings page or
   `PUT /api/config/scan`: both rewrite the whole file and drop its comments. RomM re-reads the file on every request,
   so no restart is needed — but a YAML error makes it silently fall back to all defaults, so validate the file
   (`python3 -c 'import sys, yaml; yaml.safe_load(open(sys.argv[1]))' config.yml`) and then check that `GET /api/config`
   (it needs no login) shows the new `SCAN_MEDIA` list and no `CONFIG_FILE_PARSE_ERROR`.

4. **Disk space.** Videos are large. Before scanning, tell me how much free space the Docker volume behind
   `/romm/resources` has (on a Mac that space is inside Docker Desktop's disk image, so check Docker Desktop's disk
   limit too), how many ROMs the library has, and a rough estimate of what the new media will take.

5. **Rescan.** An "Update metadata" scan with ScreenScraper selected downloads the newly enabled types for every ROM
   already matched to ScreenScraper. In the web UI it is Library → Scan: select every platform, tick ScreenScraper (and
   the other configured sources) as metadata sources, and choose "Update metadata". On 5.4.1 or later it can also be
   started with `POST /api/tasks/scan`, admin scope `tasks.run`, body
   `{"type":"update","platforms":[<platform ids>],"apis":["ss"],"roms_ids":[],"platform_fs_slugs":[],"launchbox_remote_enabled":true}`,
   and followed on `GET /api/tasks/{task_id}`. For API calls use a Client API Token I create under Administration →
   Client API Tokens and enter as `ROMM_TOKEN` (header `Authorization: Bearer $ROMM_TOKEN`). ScreenScraper has a daily
   quota: when RomM logs HTTP 430 it pauses ScreenScraper until the quota resets at midnight CET, so a large library can
   take more than one day. Tell me how far it got and whether it needs to be run again.

6. **Check the result.** Pick two or three ROMs on different platforms and show me, from
   `GET /api/roms?platform_ids=<id>&limit=3&with_char_index=false&with_filter_values=false&with_rom_id_index=false`,
   which `ss_metadata.*_path` fields are filled and the values of `path_video` and `path_manual`. Then list one ROM's
   files inside the container (`docker exec <container> ls -la /romm/resources/roms/<platform_id>/<rom_id>/`). Then
   fetch one image and the video with `curl -sI http://<host>:<port>/assets/romm/resources/<path>` and show me the
   status line and the `Content-Type`, `ETag`, `Last-Modified` and `Accept-Ranges` headers. Platforms whose ROMs have no
   `ss_metadata` at all are probably not matched to ScreenScraper; list them.

7. **The URL the Deck uses.** Tell me this Mac's Tailscale MagicDNS name (`tailscale status`), and check that
   `http://<magicdns name>:<port>/api/heartbeat` and one `/assets/romm/resources/...` file both answer through that
   name. If a reverse proxy sits in front of RomM, check that it passes `/assets/` through as well as `/api/`.

8. **Report.** Finish with: RomM's version; the providers configured (names only); the final `scan.media`; how far the
   scan got; the media each sample ROM now has; free disk space after the scan; and the exact RomM URL the Deck should
   be given.

Do not turn on `scan.gamelist.export`: Tender writes ES-DE's gamelists on the Deck itself.
