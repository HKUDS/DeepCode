import { describe, expect, it } from "vitest";
import { readableCommand } from "./sessionFolder";

const folder = "/Users/me/Test_workbench";

describe("readableCommand", () => {
  it("leaves out a cd into the Session's folder", () => {
    expect(readableCommand(`cd ${folder} && npm test`, folder)).toBe("npm test");
    expect(readableCommand(`cd ${folder}/ && git status`, folder)).toBe("git status");
    expect(readableCommand(`cd "${folder}"; ls -la`, folder)).toBe("ls -la");
    expect(readableCommand(`  cd '${folder}'  &&  python3 - <<'EOF'`, folder)).toBe(
      "python3 - <<'EOF'",
    );
  });

  it("keeps a cd into another folder, which says where the command ran", () => {
    expect(readableCommand(`cd ${folder}/web && npm test`, folder)).toBe(
      `cd ${folder}/web && npm test`,
    );
    expect(readableCommand("cd /tmp && ls", folder)).toBe("cd /tmp && ls");
  });

  it("keeps anything else as it is", () => {
    expect(readableCommand("npm test", folder)).toBe("npm test");
    expect(readableCommand(`cd ${folder}`, folder)).toBe(`cd ${folder}`);
    expect(readableCommand(`cd ${folder} && `, folder)).toBe(`cd ${folder} && `);
    expect(readableCommand(`cd ${folder} || exit 1`, folder)).toBe(
      `cd ${folder} || exit 1`,
    );
    expect(readableCommand(`cd ${folder} && npm test`, null)).toBe(
      `cd ${folder} && npm test`,
    );
  });

  it("handles Windows folders", () => {
    const windows = "C:\\Users\\me\\proj";
    expect(readableCommand(`cd "${windows}" && dir`, windows)).toBe("dir");
    expect(readableCommand(`cd ${windows}\\ && dir`, windows)).toBe("dir");
  });
});
