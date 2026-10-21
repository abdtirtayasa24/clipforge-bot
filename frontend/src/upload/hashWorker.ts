import { sha256File } from "./sha256";

const scope = self as unknown as {
  onmessage: ((event: MessageEvent<File>) => void) | null;
  postMessage(message: unknown): void;
};

scope.onmessage = (event) => {
  sha256File(event.data, (hashed, total) => {
    scope.postMessage({ type: "progress", hashed, total });
  }).then(
    (digest) => {
      scope.postMessage({ type: "done", sha256: digest });
    },
    (error: unknown) => {
      scope.postMessage({ type: "error", message: error instanceof Error ? error.message : "hashing failed" });
    },
  );
};
