import type { LibrarySurface } from "./api";

/**
 * The ES-DE settings section's read (`get_esde_status`,
 * `backend/services/esde/status.py`).
 */

/** ES-DE's scripts folder and how many of Tender's scripts are in it, or why
 *  that folder is not known. */
export type EsdeScriptsAnswer =
  | { known: true; folder: string; installed: number; expected: number }
  | { known: false; reason: string; message: string };

/** The ES-DE media folders RomM's server is not set to collect, or that it
 *  could not be established. */
export type EsdeMediaNotCollected = { established: true; folders: string[] } | { established: false; message: string };

export interface EsdeStatus {
  surface: LibrarySurface;
  scripts: EsdeScriptsAnswer;
  /** The last call ES-DE made since the backend started; `null` before its first. */
  last_call: { hook: string; at: string } | null;
  media_not_collected: EsdeMediaNotCollected;
}
