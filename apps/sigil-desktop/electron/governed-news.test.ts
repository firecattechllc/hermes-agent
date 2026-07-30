import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const root = join(import.meta.dirname, "..");

describe("governed news desktop wiring", () => {
  it("uses fixed research-only IPC channels", () => {
    const main = readFileSync(join(root, "electron", "main.ts"), "utf8");
    const preload = readFileSync(join(root, "electron", "preload.ts"), "utf8");
    const panel = readFileSync(
      join(root, "src", "mission-control", "governed-news-panel.tsx"),
      "utf8",
    );

    expect(main).toContain("sigil:get-governed-news-status");
    expect(main).toContain("sigil:get-governed-news-timeline");
    expect(main).toContain("sigil:get-governed-news-advisory-summary");
    expect(main).toContain("sigil:collect-governed-alpaca-news");
    expect(preload).toContain("getGovernedNewsStatus");
    expect(preload).toContain("collectGovernedAlpacaNews");
    expect(panel).toContain("Advisory only");
    expect(panel).toContain("No broker submission");
    expect(panel).not.toContain("APCA_API_SECRET_KEY");
    expect(panel).not.toContain("submitOrder");
  });
});
