import { describe, expect, it } from "vitest";

import { languageName, stateLabel } from "./TextDialog";

describe("languageName", () => {
  it("gives readable names for common Tesseract codes", () => {
    expect(languageName("fra")).toBe("French");
    expect(languageName("deu")).toBe("German");
    expect(languageName("swa")).toBe("Swahili");
    expect(languageName("ara")).toBe("Arabic");
  });

  it("names the script variants Tesseract adds", () => {
    expect(languageName("chi_sim")).toBe("Chinese (Simplified)");
    expect(languageName("chi_tra")).toBe("Chinese (Traditional)");
  });

  it("falls back to the code when it knows nothing better", () => {
    expect(languageName("zzz_unknown")).toBe("zzz_unknown");
  });
});

describe("stateLabel", () => {
  it("says in words where reading a file stands", () => {
    expect(stateLabel("pending")).toBe("Waiting to be read");
    expect(stateLabel("running")).toBe("Being read");
    expect(stateLabel("done")).toBe("Text ready");
    expect(stateLabel("failed")).toBe("Could not be read");
    expect(stateLabel("not_started")).toBe("Not read yet");
    expect(stateLabel("unsupported")).toContain("file type");
  });
});
