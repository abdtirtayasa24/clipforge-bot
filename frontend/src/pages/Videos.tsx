import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import type { Eligibility, RightsStatus, SourceStatus } from "../api/types";
import { type VideoFilters, useVideos } from "../api/videos";
import { Pager } from "../components/Pager";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDuration } from "../format";

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
      <h1>Videos</h1>
      <div className="row">
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
        <p className="hint">
          Showing one source's videos. <Link to="/videos">Show all</Link>
        </p>
      )}

      {videos.isPending && <Loading />}
      {videos.isError && <ErrorMessage error={videos.error} />}
      {videos.isSuccess &&
        (videos.data.items.length === 0 ? (
          <p className="hint">No videos.</p>
        ) : (
          <div className="card">
            <ul className="list">
              {videos.data.items.map((video) => (
                <li key={video.id}>
                  <Link to={`/videos/${video.id}`}>
                    <strong>{video.title}</strong>
                    <div className="hint">
                      {video.channel_title ?? video.channel_id}
                      {video.duration_seconds !== null && ` · ${formatDuration(video.duration_seconds * 1000)}`}
                    </div>
                    <div className="row">
                      <span className="badge">{video.eligibility}</span>
                      <span className="badge">rights: {video.rights_status}</span>
                      <span className="badge">{video.source_status.replace("_", " ")}</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={videos.data} onOffsetChange={setOffset} />
          </div>
        ))}
    </>
  );
}
