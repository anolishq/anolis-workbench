import { describe, expect, it } from "vitest";

import { folderUpload } from "../../src/lib/folder-upload";

// wb#749: a folder chosen in the browser becomes the body of
// POST /api/projects/import-upload: the folder's name, and each file's path
// relative to it with its bytes in base64.

function file(relativePath: string, text: string): File {
  const f = new File([text], relativePath.split("/").pop() ?? "f");
  Object.defineProperty(f, "webkitRelativePath", { value: relativePath });
  return f;
}

describe("folderUpload", () => {
  it("takes the folder from the first component and paths relative to it", async () => {
    const body = await folderUpload([
      file("rig-a/machine-profile.yaml", "schema_version: 1\n"),
      file("rig-a/config/runtime.yaml", "x"),
    ]);
    expect(body).toEqual({
      folder: "rig-a",
      files: [
        { path: "machine-profile.yaml", content: btoa("schema_version: 1\n") },
        { path: "config/runtime.yaml", content: btoa("x") },
      ],
    });
  });

  it("encodes bytes that are not text", async () => {
    const f = new File([new Uint8Array([0, 255, 128])], "b.bin");
    Object.defineProperty(f, "webkitRelativePath", { value: "rig-a/b.bin" });
    const body = await folderUpload([f]);
    expect(body.files[0].content).toBe("AP+A");
  });

  it("refuses an empty selection or files from more than one folder", async () => {
    await expect(folderUpload([])).rejects.toThrow(/No files/);
    await expect(folderUpload([file("a/x", "1"), file("b/y", "2")])).rejects.toThrow(/one folder/);
  });
});
