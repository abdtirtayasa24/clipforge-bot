import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useJobs } from "../api/jobs";
import { JOB_STATUSES, type JobStatus } from "../api/types";
import { type ChipOption, Chips } from "../components/Chips";
import { Icon } from "../components/Icon";
import { PageHeader } from "../components/PageHeader";
import { Pager } from "../components/Pager";
import { ErrorMessage, Loading } from "../components/QueryState";
import { StatusPill } from "../components/StatusPill";
import { formatDateTime } from "../format";

const STATUS_CHIPS: ChipOption<JobStatus | "all">[] = [
  { value: "all", label: "All" },
  ...JOB_STATUSES.map((status) => {
    const label = status.replaceAll("_", " ");
    return { value: status, label: label.charAt(0).toUpperCase() + label.slice(1) };
  }),
];

function parseStatus(value: string | null): JobStatus | null {
  return JOB_STATUSES.find((status) => status === value) ?? null;
}

export default function Jobs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = parseStatus(searchParams.get("status"));
  const [offset, setOffset] = useState(0);
  const jobs = useJobs(status, offset);

  function changeStatus(value: JobStatus | "all") {
    setOffset(0);
    setSearchParams(value === "all" ? {} : { status: value });
  }

  return (
    <>
      <PageHeader title="Jobs" subtitle="Monitor and manage processing jobs" />
      <Chips label="Status" options={STATUS_CHIPS} value={status ?? "all"} onChange={changeStatus} />

      {jobs.isPending && <Loading />}
      {jobs.isError && <ErrorMessage error={jobs.error} />}
      {jobs.isSuccess &&
        (jobs.data.items.length === 0 ? (
          <p className="empty">No jobs.</p>
        ) : (
          <>
            <ul className="media-list">
              {jobs.data.items.map((job) => (
                <li key={job.id}>
                  <Link className="media-card" to={`/jobs/${job.id}`}>
                    <span className="media-thumb small">
                      <Icon name="jobs" size={26} />
                    </span>
                    <span className="media-body">
                      <span className="media-top">
                        <span className="media-title">Stage: {job.stage}</span>
                        <StatusPill status={job.status} />
                      </span>
                      <span className="media-meta">
                        Created {formatDateTime(job.created_at)} · attempt {job.attempts}/{job.max_attempts}
                      </span>
                      {job.last_error && job.status === "failed" && (
                        <span className="media-meta error">{job.last_error}</span>
                      )}
                    </span>
                  </Link>
                  <p className="media-source">
                    From <Link to={`/videos/${job.video_id}`}>{job.video_title}</Link>
                  </p>
                </li>
              ))}
            </ul>
            <Pager page={jobs.data} onOffsetChange={setOffset} />
          </>
        ))}
    </>
  );
}
