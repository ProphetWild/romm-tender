/**
 * ES-DE — where a library sync puts the RomM library, and what ES-DE needs to
 * reach Tender: whether Tender's event scripts are in ES-DE's scripts folder,
 * when ES-DE last called, and which media types RomM's server leaves out. Pure
 * renderer: the page owns the surface, its change, the shortcut removal the
 * change to ES-DE offers, and the status read.
 *
 * Structure and vocabulary: `docs/architecture/qam-panel.md`, section Settings.
 */

import { FC } from "react";
import { DropdownItem, Field, PanelSection, PanelSectionRow } from "@decky/ui";
import type { EsdeStatus, LibrarySurface } from "../../types";
import { formatTimestamp, joined } from "../../utils/formatters";

/** How ES-DE's scripts are turned on, which is a step in ES-DE itself
 *  (docs/specs/esde-library.md, section 5, "Turning scripts on"). */
export const ESDE_INSTRUCTION =
  "In ES-DE, open Main menu › Other settings and turn on Enable custom event scripts, and Enable browsing custom " +
  "events for videos and manuals. Restart ES-DE after the first sync.";

/** Saves are synced at the place Tender's own choice of emulator keeps them
 *  (docs/specs/esde-library.md, D14). */
export const ESDE_EMULATOR_NOTE =
  "A game started in ES-DE with another emulator than the one Tender picks for it may sync its saves from the " +
  "wrong place.";

const SURFACE_OPTIONS: { data: LibrarySurface; label: string }[] = [
  { data: "steam", label: "Steam shortcuts" },
  { data: "esde", label: "ES-DE" },
  { data: "both", label: "Both" },
];

// The hooks ES-DE's scripts call (`backend/domain/esde_hook_scripts.py`), as
// what ES-DE was doing when it called.
const HOOK_WORDS: Record<string, string> = {
  startup: "ES-DE started",
  prepare: "a game started",
  finish: "a game ended",
  select: "a game was selected",
};

// ES-DE's media folders (`backend/domain/esde_media.py`), as a reader names them.
const MEDIA_WORDS: Record<string, string> = {
  covers: "Covers",
  marquees: "Marquees",
  screenshots: "Screenshots",
  titlescreens: "Title screens",
  "3dboxes": "3D boxes",
  backcovers: "Back covers",
  physicalmedia: "Physical media",
  fanart: "Fan art",
  miximages: "Miximages",
  videos: "Videos",
  manuals: "Manuals",
};

const NOT_WRITING = "Tender writes them while the library surface is ES-DE or Both.";

interface EsdeSectionProps {
  surface: LibrarySurface;
  onSurfaceChange: (surface: LibrarySurface) => void;
  /** The status read: `undefined` until it answers, `null` where it failed. */
  status: EsdeStatus | null | undefined;
  /** What the shortcut removal is doing or did, or `""`. */
  removal: string;
  /** The shortcut removal is under way; the surface stays as it is meanwhile. */
  removing: boolean;
}

function scriptsRow(scripts: EsdeStatus["scripts"]): { value: string; description: string } {
  if (!scripts.known) return { value: "Not known", description: scripts.message };
  if (scripts.installed === scripts.expected) return { value: "Installed", description: scripts.folder };
  if (scripts.installed === 0) return { value: "Not installed", description: `${scripts.folder}. ${NOT_WRITING}` };
  return {
    value: `${scripts.installed} of ${scripts.expected} installed`,
    description: `${scripts.folder}. The others could not be written, or a script that is not Tender's has their name.`,
  };
}

function lastCallRow(lastCall: EsdeStatus["last_call"]): { value: string; description: string } {
  if (lastCall === null) {
    return { value: "Never", description: "Not since Tender started — turn on its event scripts as below." };
  }
  return { value: formatTimestamp(lastCall.at), description: `When ${HOOK_WORDS[lastCall.hook] ?? lastCall.hook}.` };
}

function mediaRow(media: EsdeStatus["media_not_collected"]): { value: string; description: string } {
  if (!media.established) return { value: "Not known", description: media.message };
  if (media.folders.length === 0) return { value: "None", description: "RomM collects every type ES-DE shows." };
  return {
    value: joined(media.folders.map((folder) => MEDIA_WORDS[folder] ?? folder)),
    description: "Add them to scan.media in RomM's config.yml, then update the library's metadata in RomM.",
  };
}

const StatusRows: FC<{ status: EsdeStatus }> = ({ status }) => {
  const rows = [
    { label: "ES-DE event scripts", ...scriptsRow(status.scripts) },
    { label: "Last call from ES-DE", ...lastCallRow(status.last_call) },
    { label: "Media RomM does not collect", ...mediaRow(status.media_not_collected) },
  ];
  return (
    <>
      {rows.map((row) => (
        <PanelSectionRow key={row.label}>
          <Field label={row.label} description={row.description} focusable={true}>
            {row.value}
          </Field>
        </PanelSectionRow>
      ))}
    </>
  );
};

export const EsdeSection: FC<EsdeSectionProps> = ({ surface, onSurfaceChange, status, removal, removing }) => (
  <PanelSection title="ES-DE">
    <PanelSectionRow>
      <DropdownItem
        label="Library surface"
        description="Where a library sync puts your RomM library. Takes effect from the next sync."
        rgOptions={SURFACE_OPTIONS}
        selectedOption={surface}
        disabled={removing}
        onChange={(option) => onSurfaceChange(option.data as LibrarySurface)}
      />
    </PanelSectionRow>
    {removal && (
      <PanelSectionRow>
        <Field label={removal} focusable={true} />
      </PanelSectionRow>
    )}
    {status === undefined && (
      <PanelSectionRow>
        <Field label="Reading ES-DE's status…" focusable={true} />
      </PanelSectionRow>
    )}
    {status === null && (
      <PanelSectionRow>
        <Field label="ES-DE's status could not be read." focusable={true} />
      </PanelSectionRow>
    )}
    {status && <StatusRows status={status} />}
    <PanelSectionRow>
      <Field label="Turning ES-DE's scripts on" description={ESDE_INSTRUCTION} focusable={true} />
    </PanelSectionRow>
    <PanelSectionRow>
      <Field label="Saves and emulators" description={ESDE_EMULATOR_NOTE} focusable={true} />
    </PanelSectionRow>
  </PanelSection>
);
