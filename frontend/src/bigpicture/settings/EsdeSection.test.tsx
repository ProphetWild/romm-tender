import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { createElement } from "react";
import { EsdeSection, ESDE_INSTRUCTION, ESDE_EMULATOR_NOTE } from "./EsdeSection";
import type { EsdeStatus } from "../../types";

// DropdownItem isn't in the global @decky/ui stub; it is captured so the test
// can drive its onChange. Field renders its label and description and, as the
// suite's stub does, `tabindex` for a row that is a focus stop.
interface DropdownOption {
  data: unknown;
  label: string;
}
interface DropdownItemProps {
  label?: string;
  description?: string;
  rgOptions?: DropdownOption[];
  selectedOption?: unknown;
  disabled?: boolean;
  onChange?: (option: DropdownOption) => void;
}
const captured: { dropdowns: DropdownItemProps[] } = { dropdowns: [] };

vi.mock("@decky/ui", () => {
  type AnyProps = Record<string, unknown> & { children?: unknown };
  const passthrough = (tag: string) => (p: AnyProps) => createElement(tag, {}, p.children as never);
  return {
    PanelSection: passthrough("section"),
    PanelSectionRow: (p: AnyProps) => createElement("div", { "data-testid": "row" }, p.children as never),
    DropdownItem: (p: DropdownItemProps) => {
      captured.dropdowns.push(p);
      return createElement("div", { "data-testid": "dropdown", tabIndex: 0 }, p.label as never);
    },
    Field: (p: AnyProps & { label?: unknown; description?: unknown; focusable?: boolean }) =>
      createElement(
        "div",
        { "data-testid": "field", tabIndex: p.focusable ? 0 : undefined },
        createElement("span", { "data-testid": "field-label" }, p.label as never),
        createElement("span", { "data-testid": "field-desc" }, p.description as never),
        p.children as never,
      ),
  };
});

const SCRIPTS = "/home/deck/retrodeck/ES-DE/scripts";

const lastDropdown = (): DropdownItemProps => captured.dropdowns[captured.dropdowns.length - 1]!;

function status(overrides: Partial<EsdeStatus> = {}): EsdeStatus {
  return {
    surface: "esde",
    scripts: { known: true, folder: SCRIPTS, installed: 4, expected: 4 },
    last_call: { hook: "prepare", at: "2026-10-25T12:00:00+00:00" },
    media_not_collected: { established: true, folders: [] },
    ...overrides,
  };
}

function renderSection(props: Partial<Parameters<typeof EsdeSection>[0]> = {}) {
  return render(
    <EsdeSection surface="esde" onSurfaceChange={vi.fn()} status={status()} removal="" removing={false} {...props} />,
  );
}

/** The value a Field labelled *label* shows: its children, or its description where it has none. */
function field(label: string): HTMLElement {
  const found = screen
    .getAllByTestId("field")
    .find((el) => el.querySelector('[data-testid="field-label"]')?.textContent === label);
  if (!found) throw new Error(`no field labelled ${label}`);
  return found;
}

describe("EsdeSection", () => {
  beforeEach(() => {
    captured.dropdowns = [];
  });

  describe("the surface", () => {
    it("offers Steam shortcuts, ES-DE and both, with the stored one selected", () => {
      renderSection({ surface: "both" });

      const dropdown = lastDropdown();
      expect(dropdown.label).toBe("Library surface");
      expect(dropdown.rgOptions).toEqual([
        { data: "steam", label: "Steam shortcuts" },
        { data: "esde", label: "ES-DE" },
        { data: "both", label: "Both" },
      ]);
      expect(dropdown.selectedOption).toBe("both");
    });

    it("hands the picked surface on", () => {
      const onSurfaceChange = vi.fn();
      renderSection({ surface: "steam", onSurfaceChange });

      lastDropdown().onChange!({ data: "esde", label: "ES-DE" });

      expect(onSurfaceChange).toHaveBeenCalledWith("esde");
    });

    it("is dead while the shortcuts are being removed", () => {
      renderSection({ removing: true, removal: "Removing shortcuts… 3 of 10" });

      expect(lastDropdown().disabled).toBe(true);
      expect(screen.getByText("Removing shortcuts… 3 of 10")).toBeInTheDocument();
    });
  });

  describe("the scripts", () => {
    it("every one installed names the folder", () => {
      renderSection();

      expect(field("ES-DE event scripts")).toHaveTextContent("Installed");
      expect(field("ES-DE event scripts")).toHaveTextContent(SCRIPTS);
    });

    it("none installed says so", () => {
      renderSection({ status: status({ scripts: { known: true, folder: SCRIPTS, installed: 0, expected: 4 } }) });

      expect(field("ES-DE event scripts")).toHaveTextContent("Not installed");
      expect(field("ES-DE event scripts")).toHaveTextContent(
        "Tender writes them while the library surface is ES-DE or Both",
      );
    });

    it("some installed counts them", () => {
      renderSection({ status: status({ scripts: { known: true, folder: SCRIPTS, installed: 3, expected: 4 } }) });

      expect(field("ES-DE event scripts")).toHaveTextContent("3 of 4 installed");
    });

    it("a folder not known says why", () => {
      renderSection({
        status: status({
          scripts: { known: false, reason: "retrodeck_switched_off", message: "Games in ES-DE need RetroDECK." },
        }),
      });

      expect(field("ES-DE event scripts")).toHaveTextContent("Not known");
      expect(field("ES-DE event scripts")).toHaveTextContent("Games in ES-DE need RetroDECK.");
    });
  });

  describe("the last call", () => {
    it("names what ES-DE did", () => {
      renderSection();

      expect(field("Last call from ES-DE")).toHaveTextContent("a game started");
    });

    it("never says so with the instruction", () => {
      renderSection({ status: status({ last_call: null }) });

      expect(field("Last call from ES-DE")).toHaveTextContent("Never");
      expect(field("Last call from ES-DE")).toHaveTextContent("turn on its event scripts as below");
    });
  });

  describe("the media RomM does not collect", () => {
    it("lists the folders by name", () => {
      renderSection({
        status: status({ media_not_collected: { established: true, folders: ["marquees", "3dboxes", "videos"] } }),
      });

      expect(field("Media RomM does not collect")).toHaveTextContent("Marquees, 3D boxes and Videos");
    });

    it("none missing says RomM collects every type", () => {
      renderSection();

      expect(field("Media RomM does not collect")).toHaveTextContent("None");
    });

    it("a read that failed is not established, never none", () => {
      renderSection({ status: status({ media_not_collected: { established: false, message: "Cannot reach RomM." } }) });

      expect(field("Media RomM does not collect")).toHaveTextContent("Not known");
      expect(field("Media RomM does not collect")).toHaveTextContent("Cannot reach RomM.");
      expect(field("Media RomM does not collect")).not.toHaveTextContent("None");
    });
  });

  describe("while the status is not there", () => {
    it("says it is being read", () => {
      renderSection({ status: undefined });

      expect(screen.getByText("Reading ES-DE's status…")).toBeInTheDocument();
    });

    it("says a read that failed", () => {
      renderSection({ status: null });

      expect(screen.getByText("ES-DE's status could not be read.")).toBeInTheDocument();
    });
  });

  it("states how to turn ES-DE's scripts on, and the emulator caveat", () => {
    renderSection();

    expect(ESDE_INSTRUCTION).toContain("Main menu › Other settings");
    expect(ESDE_INSTRUCTION).toContain("Enable custom event scripts");
    expect(ESDE_INSTRUCTION).toContain("Enable browsing custom events");
    expect(ESDE_INSTRUCTION).toContain("Restart ES-DE after the first sync");
    expect(ESDE_EMULATOR_NOTE).toContain("another emulator");
    expect(screen.getByText(ESDE_INSTRUCTION)).toBeInTheDocument();
    expect(screen.getByText(ESDE_EMULATOR_NOTE)).toBeInTheDocument();
  });

  it("makes every row a stop Steam can focus", () => {
    renderSection({ removal: "Removed 10 shortcuts" });

    for (const row of screen.getAllByTestId("row")) {
      expect(row.querySelector('[tabindex="0"]')).not.toBeNull();
    }
  });
});
