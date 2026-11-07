import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RESOURCE_PERMISSIONS, type AclEntry } from "../api/types";
import { addEntry, entryKey } from "../pages/files/AclDialog";
import { mockApi, renderWithQuery } from "../test-utils";
import { FolderTree } from "./FolderTree";
import { ActionMenu, Dialog, describeDevice, formatBytes } from "./ui";

afterEach(() => vi.unstubAllGlobals());

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [1023, "1023 B"],
    [1024, "1.0 KB"],
    [386_080, "377 KB"],
    [5 * 1024 * 1024, "5.0 MB"],
    [3 * 1024 ** 4, "3.0 TB"],
  ])("formats %i as %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });
});

describe("access entries", () => {
  const read: AclEntry = {
    principal_type: "group",
    principal_id: "g1",
    permission: "read",
    effect: "allow",
  };

  it("identifies an entry by who, and which permission", () => {
    expect(entryKey(read)).toBe("group:g1:read");
    expect(entryKey({ ...read, effect: "deny" })).toBe("group:g1:read");
  });

  it("replaces an entry for the same person and permission instead of duplicating it", () => {
    const entries = addEntry([read], { ...read, effect: "deny" });
    expect(entries).toHaveLength(1);
    expect(entries[0]?.effect).toBe("deny");
  });

  it("keeps entries for other permissions and people", () => {
    const other: AclEntry = { ...read, principal_id: "g2" };
    expect(addEntry([read], other)).toHaveLength(2);
    expect(addEntry([read], { ...read, permission: "create" })).toHaveLength(2);
  });

  it("knows the same twelve permissions as the server", () => {
    expect(RESOURCE_PERMISSIONS.map(([code]) => code)).toEqual([
      "read",
      "preview",
      "download",
      "create",
      "edit_metadata",
      "version",
      "move",
      "annotate",
      "share",
      "delete",
      "manage_acl",
      "dispose",
    ]);
  });
});

function DialogHarness() {
  const [open, setOpen] = useState(false);
  return (
    <div>
      <button type="button" onClick={() => setOpen(true)}>
        Open it
      </button>
      {open ? (
        <Dialog title="Rename folder" onClose={() => setOpen(false)}>
          <label>
            Name
            <input type="text" />
          </label>
        </Dialog>
      ) : null}
    </div>
  );
}

describe("Dialog", () => {
  it("opens as a named modal and puts the cursor in the first field", async () => {
    const user = userEvent.setup();
    renderWithQuery(<DialogHarness />);
    await user.click(screen.getByRole("button", { name: "Open it" }));

    const dialog = await screen.findByRole("dialog", { name: "Rename folder", hidden: true });
    expect(dialog).toHaveAttribute("open");
    expect(screen.getByLabelText("Name")).toHaveFocus();
  });

  it("closes from the close button and gives focus back to what opened it", async () => {
    const user = userEvent.setup();
    renderWithQuery(<DialogHarness />);
    const opener = screen.getByRole("button", { name: "Open it" });
    await user.click(opener);
    await screen.findByRole("dialog", { name: "Rename folder", hidden: true });

    await user.click(screen.getByRole("button", { name: "Close", hidden: true }));
    await waitFor(() => expect(screen.queryByRole("dialog", { hidden: true })).not.toBeInTheDocument());
    expect(opener).toHaveFocus();
  });
});

describe("FolderTree when choosing a destination", () => {
  const cabinets = {
    items: [{ id: "c1", name: "Finance", status: "active" }],
    total: 1,
    limit: 200,
    offset: 0,
  };
  const page = { items: [], total: 0, limit: 1, offset: 0 };

  function folder(id: string, name: string) {
    return { id, name, status: "active" };
  }

  it("will not let a folder be moved into itself or anything inside it", async () => {
    const user = userEvent.setup();
    mockApi({
      "GET /cabinets": { body: cabinets },
      "GET /cabinets/c1/children": {
        body: { folders: [folder("f1", "Invoices"), folder("f2", "Orders")], documents: page },
      },
      "GET /folders/f1/children": { body: { folders: [folder("f3", "2026")], documents: page } },
    });
    renderWithQuery(<FolderTree mode="pick" disabledIds={new Set(["f1"])} onPick={() => undefined} />);

    await user.click(await screen.findByRole("button", { name: "Expand Finance" }));
    await user.click(await screen.findByRole("button", { name: "Expand Invoices" }));

    expect(await screen.findByRole("button", { name: "Invoices" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "2026" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Orders" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "Finance" })).toBeEnabled();
  });

  it("reports the chosen place", async () => {
    const user = userEvent.setup();
    const onPick = vi.fn();
    mockApi({ "GET /cabinets": { body: cabinets } });
    renderWithQuery(<FolderTree mode="pick" onPick={onPick} />);
    await user.click(await screen.findByRole("button", { name: "Finance" }));
    expect(onPick).toHaveBeenCalledWith({ kind: "cabinet", id: "c1", name: "Finance" });
  });

  it("explains an empty organization", async () => {
    mockApi({ "GET /cabinets": { body: { items: [], total: 0, limit: 200, offset: 0 } } });
    renderWithQuery(<FolderTree mode="navigate" />);
    expect(await screen.findByText(/no cabinets yet/i)).toBeInTheDocument();
  });
});

describe("ActionMenu", () => {
  function setup() {
    renderWithQuery(
      <div>
        <ActionMenu label="Actions for Invoices">
          <button type="button">Rename</button>
        </ActionMenu>
        <button type="button">Elsewhere</button>
      </div>,
    );
    const details = document.querySelector("details") as HTMLDetailsElement;
    return { details, summary: screen.getByText(/Actions/) };
  }

  it("stays open after its button is clicked", async () => {
    const user = userEvent.setup();
    const { details, summary } = setup();
    await user.click(summary);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(details.open).toBe(true);
  });

  it("closes after an action is chosen", async () => {
    const user = userEvent.setup();
    const { details, summary } = setup();
    await user.click(summary);
    await user.click(screen.getByRole("button", { name: "Rename" }));
    await waitFor(() => expect(details.open).toBe(false));
  });

  it("closes when something else is clicked, and on Escape", async () => {
    const user = userEvent.setup();
    const { details, summary } = setup();
    await user.click(summary);
    await user.click(screen.getByRole("button", { name: "Elsewhere" }));
    expect(details.open).toBe(false);

    await user.click(summary);
    expect(details.open).toBe(true);
    await user.keyboard("{Escape}");
    expect(details.open).toBe(false);
  });
});

describe("describeDevice", () => {
  it.each([
    [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36 Edg/157.0.0.0",
      "Edge on Windows",
    ],
    ["Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0", "Firefox on Linux"],
    [
      "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1",
      "Safari on iOS",
    ],
    [
      "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36",
      "Chrome on Android",
    ],
    ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Safari/605.1.15", "Safari on macOS"],
    ["curl/8.5.0", "Unknown device"],
    [null, "Unknown device"],
    ["", "Unknown device"],
  ])("names %s as %s", (agent, expected) => {
    expect(describeDevice(agent)).toBe(expected);
  });
});
