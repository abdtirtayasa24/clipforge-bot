import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useClips } from "../api/clips";
import type { ReviewStatus } from "../api/types";
import { Pager } from "../components/Pager";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatTimestamp } from "../format";

interface Tab {
  status: ReviewStatus | "all";
  label: string;
  empty: string;
}

const AWAITING_REVIEW: Tab = { status: "pending_review", label: "Awaiting review", empty: "No clips awaiting review." };
const TABS: Tab[] = [
  AWAITING_REVIEW,
  { status: "approved", label: "Approved", empty: "No approved clips." },
  { status: "rejected", label: "Rejected", empty: "No rejected clips." },
  { status: "all", label: "All", empty: "No clips." },
];

export default function Clips() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const tab = TABS.find((candidate) => candidate.status === searchParams.get("status")) ?? AWAITING_REVIEW;
  const status = tab.status === "all" ? null : tab.status;
  const clips = useClips(status, offset);

  return (
    <>
      <h1>Clips</h1>
      <div className="row" role="group" aria-label="Review status">
        {TABS.map((candidate) => (
          <button
            key={candidate.status}
            type="button"
            className={candidate === tab ? undefined : "secondary"}
            aria-pressed={candidate === tab}
            onClick={() => {
              setOffset(0);
              setSearchParams({ status: candidate.status });
            }}
          >
            {candidate.label}
          </button>
        ))}
      </div>

      {clips.isPending && <Loading />}
      {clips.isError && <ErrorMessage error={clips.error} />}
      {clips.isSuccess &&
        (clips.data.items.length === 0 ? (
          <p className="hint">{tab.empty}</p>
        ) : (
          <div className="card">
            <ul className="list">
              {clips.data.items.map((clip) => (
                <li key={clip.id}>
                  <Link to={`/clips/${clip.id}`}>
                    <strong>{clip.effective_title ?? "Untitled clip"}</strong>
                    <div className="hint">
                      {clip.video_title} · {formatTimestamp(clip.start_ms)}–{formatTimestamp(clip.end_ms)}
                    </div>
                    <div className="row">
                      <span className="badge">{clip.review_status.replace("_", " ")}</span>
                      <span className="badge">score {clip.total_score}</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={clips.data} onOffsetChange={setOffset} />
          </div>
        ))}
    </>
  );
}
