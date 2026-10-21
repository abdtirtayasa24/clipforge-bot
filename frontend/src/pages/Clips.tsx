import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useClips } from "../api/clips";
import type { ReviewStatus } from "../api/types";
import { Chips } from "../components/Chips";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { Pager } from "../components/Pager";
import { ErrorMessage, Loading } from "../components/QueryState";
import { ScoreBadge } from "../components/ScoreBadge";
import { StatusPill } from "../components/StatusPill";
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
      <PageHeader title="Clips" subtitle="Review and manage generated clips" />
      <Chips
        label="Review status"
        options={TABS.map((candidate) => ({ value: candidate.status, label: candidate.label }))}
        value={tab.status}
        onChange={(value) => {
          setOffset(0);
          setSearchParams({ status: value });
        }}
      />

      {clips.isPending && <Loading />}
      {clips.isError && <ErrorMessage error={clips.error} />}
      {clips.isSuccess &&
        (clips.data.items.length === 0 ? (
          <p className="empty">{tab.empty}</p>
        ) : (
          <>
            <ul className="media-list">
              {clips.data.items.map((clip) => (
                <li key={clip.id}>
                  <Link className="media-card" to={`/clips/${clip.id}`}>
                    <span className="media-thumb">
                      <Icon name="play" size={28} />
                      <span className="duration">{formatTimestamp(clip.end_ms - clip.start_ms)}</span>
                    </span>
                    <span className="media-body">
                      <span className="media-top">
                        <span className="media-title">{clip.effective_title ?? "Untitled clip"}</span>
                      </span>
                      <span className="media-meta">
                        {clip.video_title} · {formatTimestamp(clip.start_ms)}–{formatTimestamp(clip.end_ms)}
                      </span>
                      <span className="row">
                        <ScoreBadge score={clip.total_score} />
                        <StatusPill status={clip.review_status} />
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={clips.data} onOffsetChange={setOffset} />
          </>
        ))}
    </>
  );
}
