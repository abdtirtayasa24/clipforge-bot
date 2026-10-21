import { type FormEvent, useState } from "react";
import { Link, useParams } from "react-router";
import { ApiError } from "../api/client";
import { isActiveJob } from "../api/jobs";
import { useQueueJob, useRegisterMediaUrl, useVideo, useVideoJobs } from "../api/videos";
import { copyText } from "../clipboard";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { ErrorMessage, Loading } from "../components/QueryState";
import { StatusPill } from "../components/StatusPill";
import { UploadEntry } from "../components/UploadEntry";
import { formatBytes, formatDateTime, formatDuration } from "../format";
import { isOpen, uploadQueue, useUploads } from "../upload/uploadQueue";
import { youtubeWatchUrl } from "../youtube";
import { openExternalLink } from "../telegram/webApp";

/** Watching on YouTube is for preview and finding the creator; processing needs an authorized source. */
function YouTubeLink({ youtubeVideoId }: { youtubeVideoId: string }) {
  const url = youtubeWatchUrl(youtubeVideoId);
  const [copyState, setCopyState] = useState<{ ok: true } | { ok: false; error: unknown } | null>(null);

  async function copy() {
    try {
      await copyText(url);
      setCopyState({ ok: true });
    } catch (error) {
      setCopyState({ ok: false, error });
    }
  }

  return (
    <section className="card" aria-label="YouTube">
      <h2>YouTube</h2>
      <p className="hint url">{url}</p>
      <div className="actions">
        <button type="button" className="with-icon" onClick={() => openExternalLink(url)}>
          <Icon name="external" size={18} />
          Watch on YouTube
        </button>
        <button type="button" className="secondary with-icon" onClick={() => void copy()}>
          <Icon name="copy" size={18} />
          Copy URL
        </button>
      </div>
      {copyState?.ok && <p role="status">URL copied.</p>}
      {copyState?.ok === false && <ErrorMessage error={copyState.error} />}
    </section>
  );
}

function RegisterUrlForm({ videoId }: { videoId: string }) {
  const register = useRegisterMediaUrl(videoId);
  const [url, setUrl] = useState("");
  const errors = register.error instanceof ApiError ? register.error.fieldErrors() : {};

  function submit(event: FormEvent) {
    event.preventDefault();
    register.mutate({ url: url.trim() });
  }

  return (
    <form className="card" aria-label="Register URL" onSubmit={submit}>
      <h2>Register a permitted URL</h2>
      <p className="hint">Only hosts on the permitted download list are accepted; YouTube links are refused.</p>
      <label>
        <span>Permitted URL</span>
        <input type="url" required maxLength={2048} value={url} onChange={(event) => setUrl(event.target.value)} />
      </label>
      {errors.url && <p className="error">{errors.url}</p>}
      {register.isError && !errors.url && <ErrorMessage error={register.error} />}
      {register.isSuccess && <p role="status">URL registered.</p>}
      <button type="submit" className="block" disabled={register.isPending || url.trim() === ""}>
        Register URL
      </button>
    </form>
  );
}

function UploadForm({ videoId, videoTitle }: { videoId: string; videoTitle: string }) {
  const uploads = useUploads();
  const [file, setFile] = useState<File | null>(null);
  const [inputKey, setInputKey] = useState(0);
  const mine = uploads.filter((item) => item.videoId === videoId);
  const queueBusy = uploads.some(isOpen);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (file) {
      uploadQueue.add(videoId, videoTitle, file);
      setFile(null);
      setInputKey((key) => key + 1);
    }
  }

  return (
    <form className="card" aria-label="Upload file" onSubmit={submit}>
      <h2>Upload a file</h2>
      <p className="hint">
        Sent in 8 MiB chunks, one file at a time; an interrupted upload resumes where it stopped.
      </p>
      {mine.map((item) => (
        <UploadEntry key={item.id} item={item} />
      ))}
      <label>
        <span>Video file</span>
        <input
          key={inputKey}
          type="file"
          accept="video/*"
          onChange={(event) => setFile(event.target.files?.[0] ?? null)}
        />
      </label>
      {file && <p className="hint">{formatBytes(file.size)}</p>}
      <button type="submit" className="block" disabled={!file}>
        {queueBusy ? "Add to upload queue" : "Upload"}
      </button>
      {uploads.length > 0 && (
        <p className="hint">
          <Link to="/uploads">View all uploads</Link>
        </p>
      )}
    </form>
  );
}

function VideoJobs({ videoId }: { videoId: string }) {
  const jobs = useVideoJobs(videoId);
  const queue = useQueueJob(videoId);
  const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());
  const hasActiveJob = jobs.data?.items.some((job) => isActiveJob(job) || job.status === "awaiting_source") ?? false;

  function queueJob() {
    queue.mutate(idempotencyKey, { onSuccess: () => setIdempotencyKey(crypto.randomUUID()) });
  }

  return (
    <section className="card">
      <h2>Processing jobs</h2>
      {jobs.isPending && <Loading />}
      {jobs.isError && <ErrorMessage error={jobs.error} />}
      {jobs.isSuccess && jobs.data.items.length === 0 && <p className="hint">No jobs for this video yet.</p>}
      {jobs.isSuccess && jobs.data.items.length > 0 && (
        <ul className="list">
          {jobs.data.items.map((job) => (
            <li key={job.id}>
              <Link to={`/jobs/${job.id}`}>
                <span className="row">
                  <StatusPill status={job.status} />
                  <span>{job.stage}</span>
                  <span className="hint">{formatDateTime(job.created_at)}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="block" disabled={queue.isPending || hasActiveJob} onClick={queueJob}>
        Queue processing job
      </button>
      {hasActiveJob && <p className="hint">This video already has an active job.</p>}
      {queue.isSuccess && queue.data.status === "awaiting_source" && (
        <p role="status">Job queued; it starts once a source is registered or uploaded.</p>
      )}
      {queue.isError && <ErrorMessage error={queue.error} />}
    </section>
  );
}

export default function VideoDetail() {
  const { videoId = "" } = useParams();
  const video = useVideo(videoId);

  if (video.isPending) {
    return <Loading />;
  }
  if (video.isError) {
    return <ErrorMessage error={video.error} />;
  }
  const data = video.data;

  return (
    <>
      {data.thumbnail_url && (
        <img className="cover" src={data.thumbnail_url} alt="" referrerPolicy="no-referrer" />
      )}
      <PageHeader
        title={data.title}
        subtitle={
          <>
            {data.channel_title ?? data.channel_id}
            {data.duration_seconds !== null && ` · ${formatDuration(data.duration_seconds * 1000)}`}
            {data.view_count !== null && ` · ${data.view_count.toLocaleString()} views`}
          </>
        }
      />
      <div className="row pill-row">
        <StatusPill status={data.eligibility} />
        <StatusPill status={data.rights_status} label={`rights: ${data.rights_status}`} />
        <StatusPill status={data.source_status} />
      </div>
      <YouTubeLink youtubeVideoId={data.youtube_video_id} />
      <VideoJobs videoId={videoId} />
      {data.source_status === "awaiting_source" && (
        <>
          <RegisterUrlForm videoId={videoId} />
          <UploadForm videoId={videoId} videoTitle={data.title} />
        </>
      )}
    </>
  );
}
