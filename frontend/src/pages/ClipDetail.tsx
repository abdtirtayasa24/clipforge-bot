import { type FormEvent, useState } from "react";
import { Link, useParams } from "react-router";
import { useClip, useReviewClip } from "../api/clips";
import { ApiError } from "../api/client";
import type { CaptionOut, ClipDetail as Clip, ClipScores, ReviewDecision } from "../api/types";
import { copyText } from "../clipboard";
import { AffiliateSuggestions } from "../components/AffiliateSuggestions";
import { CostList } from "../components/CostList";
import { Icon } from "../components/Icon";
import { ErrorMessage, Loading } from "../components/QueryState";
import { StatusPill } from "../components/StatusPill";
import { formatDateTime, formatTimestamp } from "../format";
import { openExternalLink } from "../telegram/webApp";

const SCORE_NAMES: (keyof Omit<ClipScores, "total">)[] = [
  "hook",
  "emotion",
  "clarity",
  "relevance",
  "visual",
  "completeness",
];

function ReviewForm({ clip }: { clip: Clip }) {
  const review = useReviewClip(clip.id);
  const [title, setTitle] = useState(clip.effective_title ?? "");
  const [note, setNote] = useState("");
  const errors = review.error instanceof ApiError ? review.error.fieldErrors() : {};
  const editedTitle = title.trim();
  const titleChanged = editedTitle !== "" && editedTitle !== (clip.effective_title ?? "");

  function submit(decision: ReviewDecision) {
    review.mutate(
      {
        decision,
        note: note.trim() || null,
        ...(titleChanged ? { title_override: editedTitle } : {}),
      },
      { onSuccess: () => setNote("") },
    );
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    submit("approved");
  }

  return (
    <form className="card" aria-label="Review" onSubmit={onSubmit}>
      <h2>Review</h2>
      <label>
        <span>Title</span>
        <input maxLength={100} value={title} onChange={(event) => setTitle(event.target.value)} />
      </label>
      {errors.title_override && <p className="error">{errors.title_override}</p>}
      {clip.title && (clip.title_override !== null || titleChanged) && (
        <p className="hint">AI suggestion: {clip.title}</p>
      )}
      <label>
        <span>Review note</span>
        <textarea rows={2} maxLength={2000} value={note} onChange={(event) => setNote(event.target.value)} />
      </label>
      {errors.note && <p className="error">{errors.note}</p>}
      {review.isError && Object.keys(errors).length === 0 && <ErrorMessage error={review.error} />}
      <div className="actions">
        <button type="button" className="outline-danger" disabled={review.isPending} onClick={() => submit("rejected")}>
          Reject
        </button>
        <button type="submit" disabled={review.isPending}>
          Approve
        </button>
      </div>
    </form>
  );
}

function CaptionDraft({ caption }: { caption: CaptionOut }) {
  const [copyState, setCopyState] = useState<{ ok: true } | { ok: false; error: unknown } | null>(null);

  async function copy() {
    try {
      await copyText(caption.text);
      setCopyState({ ok: true });
    } catch (error) {
      setCopyState({ ok: false, error });
    }
  }

  return (
    <section className="card" aria-label="Caption draft">
      <h2>Caption / description draft</h2>
      <p className="hint">Only approved affiliate links are included, with the disclosure. Publishing stays manual.</p>
      <pre className="caption">{caption.text}</pre>
      <button type="button" className="secondary with-icon block" onClick={() => void copy()}>
        <Icon name="copy" size={18} />
        Copy caption
      </button>
      {copyState?.ok && <p role="status">Copied.</p>}
      {copyState?.ok === false && <ErrorMessage error={copyState.error} />}
    </section>
  );
}

export default function ClipDetail() {
  const { clipId = "" } = useParams();
  const clip = useClip(clipId);

  if (clip.isPending) {
    return <Loading />;
  }
  if (clip.isError) {
    return <ErrorMessage error={clip.error} />;
  }
  const data = clip.data;

  return (
    <>
      <div className="clip-hero">
        <Icon name="play" size={40} />
        <span className="duration">{formatTimestamp(data.end_ms - data.start_ms)}</span>
      </div>
      <h1 className="clip-title">{data.effective_title ?? "Untitled clip"}</h1>
      <div className="card">
        <div className="score-line">
          <span>Viral score</span>
          <div className="meter" aria-hidden="true">
            <span style={{ width: `${Math.min(100, Math.max(0, data.total_score))}%` }} />
          </div>
          <strong>{data.total_score}/100</strong>
        </div>
        <div className="row">
          <StatusPill status={data.review_status} />
          <span className="hint">
            {formatTimestamp(data.start_ms)} – {formatTimestamp(data.end_ms)}
          </span>
        </div>
        <p className="hint spaced">
          From <Link to={`/videos/${data.video_id}`}>{data.video_title}</Link> ·{" "}
          <Link to={`/jobs/${data.job_id}`}>job</Link>
        </p>
        {data.hook_line && (
          <blockquote>
            <strong>{data.hook_line}</strong>
          </blockquote>
        )}
        {data.drive_web_view_link ? (
          <>
            <button
              type="button"
              className="with-icon block"
              onClick={() => openExternalLink(data.drive_web_view_link ?? "")}
            >
              <Icon name="external" size={18} />
              Open in Google Drive
            </button>
            <p className="hint spaced">
              Opens outside Telegram; be signed in to the Google account that holds the clips.
            </p>
          </>
        ) : (
          <p className="hint">Not uploaded to Google Drive yet.</p>
        )}
      </div>

      <ReviewForm key={`${data.id}:${data.effective_title ?? ""}`} clip={data} />
      <AffiliateSuggestions clipId={data.id} />
      <CaptionDraft key={data.caption.text} caption={data.caption} />

      <section className="card">
        <h2>Why this clip</h2>
        <table aria-label="Scores">
          <tbody>
            {SCORE_NAMES.map((name) => (
              <tr key={name}>
                <td>{name}</td>
                <td>{data.scores[name]}</td>
              </tr>
            ))}
            <tr>
              <th>total</th>
              <th>{data.scores.total}</th>
            </tr>
          </tbody>
        </table>
        {data.explanation && <p>{data.explanation}</p>}
        {data.transcript_excerpt && (
          <>
            <h3>Transcript excerpt</h3>
            <p className="hint">{data.transcript_excerpt}</p>
          </>
        )}
      </section>

      <section className="card" aria-label="Estimated AI cost">
        <h2>Estimated AI cost of the job</h2>
        <CostList costs={data.job_costs} />
      </section>

      {data.reviews.length > 0 && (
        <section className="card">
          <h2>Review history</h2>
          <ul className="list">
            {data.reviews.map((entry) => (
              <li key={entry.id}>
                <StatusPill status={entry.decision} /> <span className="hint">{formatDateTime(entry.created_at)}</span>
                {entry.title_override && <div className="hint">Title: {entry.title_override}</div>}
                {entry.note && <div>{entry.note}</div>}
              </li>
            ))}
          </ul>
        </section>
      )}
    </>
  );
}
