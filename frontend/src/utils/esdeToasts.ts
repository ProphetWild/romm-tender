/**
 * The toasts a game started from ES-DE raises: what Tender did when ES-DE's
 * event scripts called it, pushed as `esde_launch` on a start and `esde_session`
 * on an end (`backend/services/esde/hooks.py`).
 *
 * ES-DE gives a script no way to draw on its own screen, so Steam's
 * notifications are the only place a download, a save pull or a post-exit sync
 * started from ES-DE can be said. The words for what moved are the session
 * manager's (`saveSyncToastBody`), and what went wrong is the backend's own
 * sentence, as on a game's end in Steam.
 */

import { addEventListener, removeEventListener } from "../api/host";
import type { SessionFinalizeResult } from "../api/backend";
import { setMigrationStatus } from "./migrationStore";
import { announceSaveSync } from "./saveSyncEvents";
import { saveSyncToastBody } from "./saveSyncToast";
import { PRUNE_ACTIVE_FINALIZE_TOAST_BODY } from "./sessionManager";
import { showToast } from "./toast";

/** What a start's save pull did, in the shape a session's end reports its sync in. */
export interface EsdeSaveSync {
  success: boolean;
  uploaded: number;
  downloaded: number;
  failure_toast: string | null;
  conflicts_toast: string | null;
}

/** One step of a start from ES-DE, for the game `rom_id` named `name`. */
export type EsdeLaunchEvent =
  | { stage: "downloading"; rom_id: number; name: string }
  | { stage: "not_downloaded"; rom_id: number; name: string; message: string }
  | { stage: "listed_elsewhere"; rom_id: number; name: string }
  | { stage: "saves"; rom_id: number; name: string; sync: EsdeSaveSync };

/** A game's end in ES-DE: the session's finalize result, or the refusal that kept it from running. */
export type EsdeSessionEvent =
  | { rom_id: number; name: string; result: SessionFinalizeResult }
  | { rom_id: number; name: string; refusal: { reason: string; message: string } };

function toastSync(sync: EsdeSaveSync): void {
  const directional = sync.success ? saveSyncToastBody(sync.uploaded, sync.downloaded) : null;
  if (directional) {
    showToast(directional);
  } else if (sync.failure_toast) {
    showToast(sync.failure_toast);
  }
  if (sync.conflicts_toast) showToast(sync.conflicts_toast);
}

function onLaunch(event: EsdeLaunchEvent): void {
  switch (event.stage) {
    case "downloading":
      showToast(`Downloading ${event.name}…`);
      return;
    case "not_downloaded":
      showToast(`Couldn't download ${event.name}`, { subtext: event.message });
      return;
    case "listed_elsewhere":
      showToast(`Restart ES-DE to start ${event.name}`, {
        subtext: "It was downloaded under another name than the one ES-DE lists.",
      });
      return;
    case "saves":
      toastSync(event.sync);
      announceSaveSync(event.rom_id);
      return;
  }
}

function onSession(event: EsdeSessionEvent): void {
  if ("refusal" in event) {
    showToast(event.refusal.reason === "prune_active" ? PRUNE_ACTIVE_FINALIZE_TOAST_BODY : event.refusal.message);
    return;
  }
  toastSync(event.result.sync);
  announceSaveSync(event.rom_id);
  // `null` is a refresh that failed, which must not clear a pending badge it could not re-check.
  if (event.result.migration) setMigrationStatus(event.result.migration.retrodeck);
}

/** Raise the toasts for every start and end ES-DE reports from here on; answers the unsubscribe. */
export function watchEsdeToasts(): () => void {
  const launch = addEventListener<EsdeLaunchEvent>("esde_launch", onLaunch);
  const session = addEventListener<EsdeSessionEvent>("esde_session", onSession);
  return () => {
    removeEventListener("esde_launch", launch);
    removeEventListener("esde_session", session);
  };
}
