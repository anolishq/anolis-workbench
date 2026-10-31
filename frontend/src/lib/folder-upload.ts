// folder-upload.ts: a folder chosen in the browser (an <input webkitdirectory>)
// as the body of POST /api/projects/import-upload (wb#749). The server checks
// every path and the limits; this only shapes the request.

export interface FolderUpload {
  folder: string;
  files: { path: string; content: string }[];
}

function base64(bytes: Uint8Array): string {
  let text = "";
  // In chunks: String.fromCharCode with one argument per byte overflows the
  // call stack on a large file.
  for (let i = 0; i < bytes.length; i += 0x8000) {
    text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(text);
}

/** Each file's path relative to the chosen folder, and its bytes in base64. */
export async function folderUpload(files: ArrayLike<File>): Promise<FolderUpload> {
  const list = Array.from(files);
  if (!list.length) throw new Error("No files: choose a project folder.");
  let folder = "";
  const out: FolderUpload["files"] = [];
  for (const f of list) {
    const [top, ...rest] = (f.webkitRelativePath || f.name).split("/");
    if (folder && top !== folder)
      throw new Error("Choose one folder: these files are from more than one.");
    folder = top;
    out.push({ path: rest.join("/"), content: base64(new Uint8Array(await f.arrayBuffer())) });
  }
  return { folder, files: out };
}
