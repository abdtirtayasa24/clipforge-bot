import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { uploadKeys, useActiveUpload } from "../api/uploads";
import type { UploadOut } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { ErrorMessage, Loading } from "../components/QueryState";
import { UploadEntry } from "../components/UploadEntry";
import { formatBytes } from "../format";
import { isOpen, uploadQueue, useUploads } from "../upload/uploadQueue";

/** An upload the server is receiving that no entry here tracks, e.g. one started on another device. */
function UntrackedUpload({ upload }: { upload: UploadOut }) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const [error, setError] = useState<unknown>(null);

  async function cancel() {
    setCancelling(true);
    setError(null);
    try {
      await uploadQueue.cancelServerUpload(upload.id);
      await queryClient.invalidateQueries({ queryKey: uploadKeys.active });
    } catch (cancelError) {
      setError(cancelError);
    } finally {
      setCancelling(false);
    }
  }

  return (
    <section className="card" aria-label="Paused upload">
      <h2>Paused upload</h2>
      <p className="hint">
        {upload.filename ?? "Unnamed file"} · {formatBytes(upload.received_bytes)} /{" "}
        {formatBytes(upload.size_bytes ?? 0)}
      </p>
      <p>
        This upload for <Link to={`/videos/${upload.video_id}`}>this video</Link> was started on another device. Resume
        it there, or cancel it so the queue can continue.
      </p>
      <button type="button" className="secondary block" disabled={cancelling} onClick={() => void cancel()}>
        Cancel upload
      </button>
      {error !== null && <ErrorMessage error={error} />}
    </section>
  );
}

export default function Uploads() {
  const items = useUploads();
  const active = useActiveUpload();
  const untracked =
    active.data && !items.some((item) => isOpen(item) && item.uploadId === active.data?.id) ? active.data : null;
  const queued = items.filter((item) => item.state === "queued");

  return (
    <>
      <PageHeader title="Uploads" subtitle="One file uploads at a time; the rest wait in line" />
      {active.isPending && <Loading />}
      {active.isError && <ErrorMessage error={active.error} />}
      {untracked && <UntrackedUpload upload={untracked} />}
      {items.length === 0 && !untracked && !active.isPending && <p className="empty">No uploads.</p>}
      {items.map((item) => (
        <section key={item.id} className="card" aria-label={item.videoTitle}>
          <h2>
            <Link to={`/videos/${item.videoId}`}>{item.videoTitle}</Link>
          </h2>
          <UploadEntry item={item} position={item.state === "queued" ? queued.indexOf(item) + 1 : undefined} />
        </section>
      ))}
      {items.some((item) => !isOpen(item)) && (
        <button type="button" className="secondary block" onClick={() => uploadQueue.clearFinished()}>
          Clear finished
        </button>
      )}
    </>
  );
}
