import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { Eligibility, RightsStatus, SourceStatus } from "../api/types";
import { type VideoFilters, useVideos } from "../api/videos";
import { PageHeader } from "../components/PageHeader";
import { Pager } from "../components/Pager";
import { ErrorMessage, Loading } from "../components/QueryState";
import { StatusPill } from "../components/StatusPill";
import { VideoThumb } from "../components/VideoThumb";

const FILTERS = {
  eligibility: { label: "Eligibility", options: ["pending", "eligible", "ineligible"] },
  rights_status: { label: "Rights", options: ["unverified", "authorized", "rejected"] },
  source_status: { label: "Source", options: ["awaiting_source", "ready"] },
} as const;

type FilterName = keyof typeof FILTERS;

function pick<T extends string>(options: readonly T[], value: string | null): T | null {
  return options.find((option) => option === value) ?? null;
}

export default function Videos() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [offset, setOffset] = useState(0);
  const filters: VideoFilters = {
    eligibility: pick<Eligibility>(FILTERS.eligibility.options, searchParams.get("eligibility")),
    rights_status: pick<RightsStatus>(FILTERS.rights_status.options, searchParams.get("rights_status")),
    source_status: pick<SourceStatus>(FILTERS.source_status.options, searchParams.get("source_status")),
    source_id: searchParams.get("source_id"),
  };
  const videos = useVideos(filters, offset);

  function setFilter(name: FilterName, value: string) {
    const next = new URLSearchParams(searchParams);
    if (value) {
      next.set(name, value);
    } else {
      next.delete(name);
    }
    setOffset(0);
    setSearchParams(next);
  }

  return (
    <>
      <PageHeader title="Videos" subtitle="Discovered videos and their source status" />
      <div className="filters">
        {(Object.keys(FILTERS) as FilterName[]).map((name) => (
          <label key={name}>
            <span>{FILTERS[name].label}</span>
            <select value={filters[name] ?? ""} onChange={(event) => setFilter(name, event.target.value)}>
              <option value="">All</option>
              {FILTERS[name].options.map((option) => (
                <option key={option} value={option}>
                  {option.replace("_", " ")}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>
      {filters.source_id && (
        <p className="notice">
          <span>
            Showing one source's videos. <Link to="/videos">Show all</Link>
          </span>
        </p>
      )}

      {videos.isPending && <Loading />}
      {videos.isError && <ErrorMessage error={videos.error} />}
      {videos.isSuccess &&
        (videos.data.items.length === 0 ? (
          <p className="empty">No videos.</p>
        ) : (
          <>
            <ul className="media-list">
              {videos.data.items.map((video) => (
                <li key={video.id}>
                  <Link className="media-card" to={`/videos/${video.id}`}>
                    <VideoThumb video={video} />
                    <span className="media-body">
                      <span className="media-title">{video.title}</span>
                      <span className="media-meta">{video.channel_title ?? video.channel_id}</span>
                      <span className="row">
                        <StatusPill status={video.eligibility} />
                        <StatusPill status={video.rights_status} label={`rights: ${video.rights_status}`} />
                        <StatusPill status={video.source_status} />
                      </span>
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={videos.data} onOffsetChange={setOffset} />
          </>
        ))}
    </>
  );
}
