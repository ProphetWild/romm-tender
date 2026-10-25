/**
 * The ES-DE section's state and handlers, held by the Settings page: the
 * library surface and its change, the offer to remove Tender's Steam shortcuts
 * that the change to ES-DE makes, and the status read, asked each time the
 * section is opened since ES-DE's last call moves while the page is open.
 */

import { useCallback, useEffect, useState } from "react";
import { ConfirmModal, showModal } from "@decky/ui";
import { getEsdeStatus, logError, setLibrarySurface } from "../../api/backend";
import type { EsdeStatus, LibrarySurface } from "../../types";
import { detach } from "../../utils/detach";
import { usePruneLeaseOwner } from "../../utils/pruneLease";
import { removeEveryTenderShortcut, type AllShortcutsRemoval } from "../../utils/removeAllShortcuts";

// Process-local: a key in `pruneLease`'s own map, unique among the pages that hold leases.
const ESDE_SETTINGS_LEASE_OWNER = "settings-esde";

export interface EsdeSettings {
  surface: LibrarySurface;
  /** The surface the settings read answered. The same function on every
   *  render, so an effect may name it without running again. */
  loadSurface: (surface: LibrarySurface) => void;
  changeSurface: (surface: LibrarySurface) => void;
  /** `undefined` until the read answers, `null` where it failed. */
  status: EsdeStatus | null | undefined;
  removal: string;
  removing: boolean;
}

function removalLine(outcome: Exclude<AllShortcutsRemoval, { kind: "cancelled" }>): string {
  switch (outcome.kind) {
    case "refused":
      return outcome.message ?? "The shortcuts could not be removed.";
    case "removed":
      return outcome.message ?? "All shortcuts removed";
    case "failed":
      return "Failed to remove shortcuts";
  }
}

export function useEsdeSettings(open: boolean): EsdeSettings {
  const [surface, setSurface] = useState<LibrarySurface>("steam");
  const [status, setStatus] = useState<EsdeStatus | null | undefined>(undefined);
  const [removal, setRemoval] = useState("");
  const [removing, setRemoving] = useState(false);
  usePruneLeaseOwner(ESDE_SETTINGS_LEASE_OWNER);

  const readStatus = useCallback(
    () =>
      getEsdeStatus()
        .then(setStatus)
        .catch((e: unknown) => {
          logError(`Failed to read the ES-DE status: ${e}`);
          setStatus(null);
        }),
    [],
  );

  useEffect(() => {
    if (open) detach(readStatus());
  }, [open, readStatus]);

  const removeShortcuts = async () => {
    // A disabled control still reports a press on the device.
    if (removing) return;
    setRemoving(true);
    setRemoval("Removing shortcuts…");
    const outcome = await removeEveryTenderShortcut(ESDE_SETTINGS_LEASE_OWNER, (removed, total) =>
      setRemoval(`Removing shortcuts… ${removed} of ${total}`),
    );
    // A cancelled removal is the page's teardown: nothing is left to tell.
    if (outcome.kind === "cancelled") return;
    setRemoval(removalLine(outcome));
    setRemoving(false);
  };

  const offerRemoval = () => {
    showModal(
      <ConfirmModal
        strTitle="Remove Tender's Steam shortcuts?"
        strDescription={
          "ES-DE now lists your RomM library, so the shortcuts Tender made in Steam are no longer needed. " +
          "Removing them takes them and Tender's Steam collections out of Steam; your downloads and saves stay. " +
          "Kept, they stay as they are, and Data Management can remove them later."
        }
        strOKButtonText="Remove shortcuts"
        strCancelButtonText="Keep them"
        onOK={() => detach(removeShortcuts())}
      />,
    );
  };

  const changeSurface = (next: LibrarySurface) => {
    const previous = surface;
    if (next === previous || removing) return;
    setSurface(next);
    detach(
      (async () => {
        try {
          const result = await setLibrarySurface(next);
          if (!result.success) {
            setSurface(previous);
            return;
          }
        } catch (e) {
          logError(`Failed to save the library surface: ${e}`);
          setSurface(previous);
          return;
        }
        // The scripts follow the surface stored, so what the rows say has moved.
        detach(readStatus());
        if (next === "esde") offerRemoval();
      })(),
    );
  };

  return { surface, loadSurface: setSurface, changeSurface, status, removal, removing };
}
