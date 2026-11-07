// Regenerates src/api/schema.d.ts from the running code of the API. CI runs this and fails if
// the committed file differs, so the client can never drift from the backend.
import { execFileSync } from "node:child_process";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const candidates = [
  process.env.PYTHON,
  "../.venv/Scripts/python.exe",
  "../.venv/bin/python",
  "python3",
  "python",
].filter(Boolean);
const python = candidates.find((c) => !c.includes("/") || existsSync(c)) ?? "python";

const json = execFileSync(python, ["-I", "../backend/scripts/dump_openapi.py"], {
  encoding: "utf8",
  maxBuffer: 32 * 1024 * 1024,
});
const dir = mkdtempSync(join(tmpdir(), "docuflow-openapi-"));
const file = join(dir, "openapi.json");
writeFileSync(file, json);
execFileSync("npx", ["openapi-typescript", file, "-o", "src/api/schema.d.ts"], {
  stdio: "inherit",
  shell: process.platform === "win32",
});
