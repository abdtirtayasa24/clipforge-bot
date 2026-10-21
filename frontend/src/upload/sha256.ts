import { sha256 } from "@noble/hashes/sha2.js";
import { bytesToHex } from "@noble/hashes/utils.js";

export const HASH_SLICE_BYTES = 8 * 1024 * 1024;

/** Incremental SHA-256 so multi-GB files never sit in memory at once (WebCrypto can't stream). */
export async function sha256File(
  file: Blob,
  onProgress?: (hashedBytes: number, totalBytes: number) => void,
  sliceBytes = HASH_SLICE_BYTES,
): Promise<string> {
  const hash = sha256.create();
  for (let offset = 0; offset < file.size; offset += sliceBytes) {
    const end = Math.min(offset + sliceBytes, file.size);
    hash.update(new Uint8Array(await file.slice(offset, end).arrayBuffer()));
    onProgress?.(end, file.size);
  }
  return bytesToHex(hash.digest());
}

type WorkerReply =
  | { type: "progress"; hashed: number; total: number }
  | { type: "done"; sha256: string }
  | { type: "error"; message: string };

/** Hashes off the main thread so the Mini App stays responsive on phones. */
export function sha256InWorker(file: File, onProgress?: (hashed: number, total: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const worker = new Worker(new URL("./hashWorker.ts", import.meta.url), { type: "module" });
    worker.onmessage = (event: MessageEvent<WorkerReply>) => {
      const reply = event.data;
      if (reply.type === "progress") {
        onProgress?.(reply.hashed, reply.total);
        return;
      }
      worker.terminate();
      if (reply.type === "done") {
        resolve(reply.sha256);
      } else {
        reject(new Error(reply.message));
      }
    };
    worker.onerror = (event) => {
      worker.terminate();
      reject(new Error(event.message || "hashing failed"));
    };
    worker.postMessage(file);
  });
}
