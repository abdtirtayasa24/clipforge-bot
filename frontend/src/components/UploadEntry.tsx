import { useState } from "react";
import { formatBytes } from "../format";
import { type UploadItem, uploadQueue } from "../upload/uploadQueue";
import { ErrorMessage } from "./QueryState";

function Progress({ label, item }: { label: string; item: UploadItem }) {
  return (
    <div role="status">
      <span>{label}</span>
      <progress max={item.size} value={item.done} aria-label={label} />
      <span className="hint">
        {formatBytes(item.done)} / {formatBytes(item.size)}
      </span>
    </div>
  );
}

/** Status and actions for one queued file; shared by the video page and the Uploads page. */
export function UploadEntry({ item, position }: { item: UploadItem; position?: number }) {
  const [removeError, setRemoveError] = useState<unknown>(null);
  const [removing, setRemoving] = useState(false);

  async function remove() {
    setRemoving(true);
    setRemoveError(null);
    try {
      await uploadQueue.remove(item.id);
    } catch (error) {
      setRemoveError(error);
      setRemoving(false);
    }
  }

  return (
    <div>
      <p className="hint">
        {item.filename} · {formatBytes(item.size)}
      </p>
      {item.state === "queued" && (
        <p role="status">{position === undefined ? "Queued." : `Queued · #${position} in line`}</p>
      )}
      {item.state === "hashing" && <Progress label="Checking file…" item={item} />}
      {item.state === "uploading" && <Progress label="Uploading…" item={item} />}
      {item.state === "complete" && <p role="status">Upload complete.</p>}
      {(item.state === "interrupted" || item.state === "failed") && item.error && (
        <p role="alert" className="error">
          {item.error}
        </p>
      )}
      {item.state === "needs_file" && (
        <label>
          <span>Paused. Select the same file again to continue.</span>
          <input
            type="file"
            accept="video/*"
            aria-label={`Select ${item.filename} again`}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                uploadQueue.attachFile(item.id, file);
              }
            }}
          />
        </label>
      )}
      <div className="actions">
        {item.state === "interrupted" && (
          <button type="button" onClick={() => uploadQueue.retry(item.id)}>
            Resume upload
          </button>
        )}
        {item.state === "failed" && uploadQueue.hasFile(item.id) && (
          <button type="button" onClick={() => uploadQueue.retry(item.id)}>
            Retry
          </button>
        )}
        {item.state !== "complete" && (
          <button type="button" className="secondary" disabled={removing} onClick={() => void remove()}>
            {item.state === "failed" ? "Remove" : "Cancel upload"}
          </button>
        )}
      </div>
      {removeError !== null && <ErrorMessage error={removeError} />}
    </div>
  );
}
