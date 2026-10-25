/**
 * Removing every Steam shortcut of Tender's: the backend's bound list and what
 * the live scan finds beside it, then Tender's Steam collections, then the
 * report that unbinds the rows — all under the removal's prune lease, held by
 * the page that pressed.
 *
 * Data Management's Remove all and the switch to ES-DE in Settings both run it;
 * each words the outcome itself.
 */

import { logInfo, logWarn, removeAllShortcuts, reportRemovalResults } from "../api/backend";
import { clearAllRomMCollections } from "./collections";
import {
  capturePruneLeaseAdmission,
  isPruneLeaseCancellation,
  isPruneLeaseCancelled,
  withPruneLease,
} from "./pruneLease";
import { removeShortcutsPaced } from "./shortcutRemoval";
import { getLiveRomMShortcutAppIds } from "./steamShortcuts";
import { withTimeout } from "./withTimeout";

const REMOVAL_REPORT_TIMEOUT_MS = 15000;

/** How a removal of every shortcut ended. `cancelled` is the owner's teardown,
 *  not a failure: the backend work already committed and nobody is left to
 *  tell. `removedCount` is how many shortcuts Steam was asked to remove. */
export type AllShortcutsRemoval =
  | { kind: "refused"; message: string | undefined }
  | { kind: "removed"; message: string | undefined; removedCount: number }
  | { kind: "failed"; removedCount: number }
  | { kind: "cancelled" };

/**
 * Remove every shortcut of Tender's under the prune lease of *owner*, which the
 * caller has mounted. *onProgress* is told how many of how many are removed;
 * *onAccepted* runs once the backend has taken the removal, before Steam is
 * touched.
 */
export async function removeEveryTenderShortcut(
  owner: string,
  onProgress: (removed: number, total: number) => void,
  onAccepted: () => void = () => {},
): Promise<AllShortcutsRemoval> {
  let removedCount = 0;
  const admission = capturePruneLeaseAdmission(owner);
  try {
    const result = await removeAllShortcuts();
    if (!result.success) {
      // A refusal by the update, migration, sync or prune rule carries no
      // app_ids/rom_ids, so nothing is removed.
      return { kind: "refused", message: result.message };
    }
    onAccepted();
    // The backend list is the DB binding map (roms.shortcut_app_id). A crashed
    // sync run's in-flight chunk can leave RomM-owned shortcuts in Steam (exe =
    // bin/tender-rom-launcher) that were never committed — no binding, so the
    // backend never returns them. The live exe-ownership scan sees them; remove
    // the UNION so no orphan is left behind.
    const backendAppIds = result.app_ids ?? [];
    const removed = new Set<number>(backendAppIds);
    await withPruneLease(
      result.prune_lease_token,
      "All-shortcut removal",
      async (signal) => {
        await removeShortcutsPaced(backendAppIds, onProgress, signal);
        if (isPruneLeaseCancelled(signal)) return;
        const liveAppIds = await getLiveRomMShortcutAppIds();
        if (isPruneLeaseCancelled(signal)) return;
        if (liveAppIds === null) {
          // The scan could not run (Steam's shortcut store was unreadable) —
          // fall back to the backend-bound list alone rather than skip removal.
          logWarn("Live RomM shortcut scan unavailable — removed backend-bound shortcuts only.");
        } else {
          const orphans = liveAppIds.filter((appId) => !removed.has(appId));
          logInfo(`Remove-all: ${orphans.length} live-scanned RomM shortcut(s) were not in the backend list.`);
          // The counter carries on across the orphan sweep, offset by the
          // backend count, so "removed of total" stays cumulative.
          await removeShortcutsPaced(
            orphans,
            (done, orphanTotal) => onProgress(backendAppIds.length + done, backendAppIds.length + orphanTotal),
            signal,
          );
          if (isPruneLeaseCancelled(signal)) return;
          for (const appId of orphans) removed.add(appId);
        }
        removedCount = removed.size;
        await clearAllRomMCollections(signal);
        if (isPruneLeaseCancelled(signal)) return;
        // rom_ids are backend DB rows — orphans have none, so only the backend
        // set is reported.
        if (result.rom_ids?.length || result.prune_lease_token) {
          await withTimeout(
            reportRemovalResults(result.rom_ids ?? [], result.prune_lease_token ?? null),
            REMOVAL_REPORT_TIMEOUT_MS,
          );
        }
      },
      owner,
      admission,
    );
    return { kind: "removed", message: result.message, removedCount };
  } catch (e) {
    if (isPruneLeaseCancellation(e, admission)) {
      logWarn(`All-shortcut removal continuation was cancelled: ${e}`);
      return { kind: "cancelled" };
    }
    return { kind: "failed", removedCount };
  }
}
