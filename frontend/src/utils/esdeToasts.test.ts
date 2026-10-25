import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { toaster } from "../api/host";
import { emitHostEvent, hostEventListenerCount } from "../test-utils/host-event-bus";
import { watchEsdeToasts, type EsdeLaunchEvent, type EsdeSessionEvent } from "./esdeToasts";
import { getMigrationState, setMigrationStatus } from "./migrationStore";
import { PRUNE_ACTIVE_FINALIZE_TOAST_BODY } from "./sessionManager";

const raised = () => vi.mocked(toaster.toast).mock.calls.map(([toast]) => toast);

const synced = {
  success: true,
  uploaded: 0,
  downloaded: 0,
  failure_toast: null,
  conflicts_toast: null,
} as const;

function launch(event: EsdeLaunchEvent): void {
  emitHostEvent<EsdeLaunchEvent>("esde_launch", event);
}

function session(event: EsdeSessionEvent): void {
  emitHostEvent<EsdeSessionEvent>("esde_session", event);
}

describe("the toasts a game started from ES-DE raises", () => {
  let stop: () => void = () => {};
  let saveSyncs: number[] = [];
  const onDataChanged = (event: Event) => {
    const detail = (event as CustomEvent<{ type: string; rom_id: number }>).detail;
    if (detail.type === "save_sync") saveSyncs.push(detail.rom_id);
  };

  beforeEach(() => {
    vi.mocked(toaster.toast).mockClear();
    saveSyncs = [];
    globalThis.addEventListener("romm_data_changed", onDataChanged);
    stop = watchEsdeToasts();
  });

  afterEach(() => {
    stop();
    globalThis.removeEventListener("romm_data_changed", onDataChanged);
    setMigrationStatus({ pending: false });
  });

  describe("on the start", () => {
    it("says a game is being downloaded", () => {
      launch({ stage: "downloading", rom_id: 7, name: "Game (USA)" });

      expect(raised()).toEqual([{ title: "Tender", body: "Downloading Game (USA)…" }]);
    });

    it("says a game was not downloaded, with why", () => {
      launch({ stage: "not_downloaded", rom_id: 7, name: "Game (USA)", message: "Not enough disk space" });

      expect(raised()).toEqual([
        { title: "Tender", body: "Couldn't download Game (USA)", subtext: "Not enough disk space" },
      ]);
    });

    it("says ES-DE must be restarted for a game downloaded under another name", () => {
      launch({ stage: "listed_elsewhere", rom_id: 7, name: "Game (USA)" });

      expect(raised()).toEqual([
        {
          title: "Tender",
          body: "Restart ES-DE to start Game (USA)",
          subtext: "It was downloaded under another name than the one ES-DE lists.",
        },
      ]);
    });

    it("names the direction saves moved in, and refreshes what shows the game's saves", () => {
      launch({ stage: "saves", rom_id: 7, name: "Game (USA)", sync: { ...synced, downloaded: 1 } });

      expect(raised()).toEqual([{ title: "Tender", body: "Saves downloaded from RomM" }]);
      expect(saveSyncs).toEqual([7]);
    });

    it("says nothing for a pull that moved nothing", () => {
      launch({ stage: "saves", rom_id: 7, name: "Game (USA)", sync: synced });

      expect(raised()).toEqual([]);
      expect(saveSyncs).toEqual([7]);
    });

    it("says what kept the pull from running", () => {
      launch({
        stage: "saves",
        rom_id: 7,
        name: "Game (USA)",
        sync: { ...synced, success: false, failure_toast: "RomM is offline, so the game starts with the saves here." },
      });

      expect(raised()).toEqual([{ title: "Tender", body: "RomM is offline, so the game starts with the saves here." }]);
    });

    it("says the conflicts beside the direction", () => {
      launch({
        stage: "saves",
        rom_id: 7,
        name: "Game (USA)",
        sync: { ...synced, uploaded: 1, conflicts_toast: "1 save conflict needs resolution" },
      });

      expect(raised()).toEqual([
        { title: "Tender", body: "Saves uploaded to RomM" },
        { title: "Tender", body: "1 save conflict needs resolution" },
      ]);
    });
  });

  describe("on the end", () => {
    const result = {
      total_seconds: 120,
      sync: {
        offline: false,
        success: true,
        synced: 1,
        uploaded: 1,
        downloaded: 0,
        conflicts: [],
        failure_toast: null,
        conflicts_toast: null,
      },
      migration: null,
    };

    it("words the post-exit sync as the session manager does", () => {
      session({ rom_id: 7, name: "Game (USA)", result });

      expect(raised()).toEqual([{ title: "Tender", body: "Saves uploaded to RomM" }]);
      expect(saveSyncs).toEqual([7]);
    });

    it("says a failed post-exit sync with the backend's words", () => {
      session({
        rom_id: 7,
        name: "Game (USA)",
        result: {
          ...result,
          sync: {
            ...result.sync,
            success: false,
            offline: true,
            failure_toast: "Server offline — saves will sync next time",
          },
        },
      });

      expect(raised()).toEqual([{ title: "Tender", body: "Server offline — saves will sync next time" }]);
    });

    it("feeds the migration status it read into the store", () => {
      session({ rom_id: 7, name: "Game (USA)", result: { ...result, migration: { retrodeck: { pending: true } } } });

      expect(getMigrationState()).toEqual({ pending: true });
    });

    it("says a cleanup kept the session from ending as the session manager does", () => {
      session({ rom_id: 7, name: "Game (USA)", refusal: { reason: "prune_active", message: "A cleanup is running" } });

      expect(raised()).toEqual([{ title: "Tender", body: PRUNE_ACTIVE_FINALIZE_TOAST_BODY }]);
    });

    it("says any other refusal in its own words", () => {
      session({ rom_id: 7, name: "Game (USA)", refusal: { reason: "blocked_by_update", message: "Updating." } });

      expect(raised()).toEqual([{ title: "Tender", body: "Updating." }]);
    });
  });

  it("stops listening when asked", () => {
    stop();

    expect(hostEventListenerCount("esde_launch")).toBe(0);
    expect(hostEventListenerCount("esde_session")).toBe(0);
  });
});
