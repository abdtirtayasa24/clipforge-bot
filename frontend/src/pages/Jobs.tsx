import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { useJobs } from "../api/jobs";
import { JOB_STATUSES, type JobStatus } from "../api/types";
import { Pager } from "../components/Pager";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../format";

function parseStatus(value: string | null): JobStatus | null {
  return JOB_STATUSES.find((status) => status === value) ?? null;
}

export default function Jobs() {
  const [searchParams, setSearchParams] = useSearchParams();
  const status = parseStatus(searchParams.get("status"));
  const [offset, setOffset] = useState(0);
  const jobs = useJobs(status, offset);

  function changeStatus(value: string) {
    setOffset(0);
    setSearchParams(value ? { status: value } : {});
  }

  return (
    <>
      <h1>Jobs</h1>
      <label>
        <span>Status</span>
        <select value={status ?? ""} onChange={(event) => changeStatus(event.target.value)}>
          <option value="">All</option>
          {JOB_STATUSES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </label>

      {jobs.isPending && <Loading />}
      {jobs.isError && <ErrorMessage error={jobs.error} />}
      {jobs.isSuccess &&
        (jobs.data.items.length === 0 ? (
          <p className="hint">No jobs.</p>
        ) : (
          <div className="card">
            <ul className="list">
              {jobs.data.items.map((job) => (
                <li key={job.id}>
                  <Link to={`/jobs/${job.id}`}>
                    <span className="badge">{job.status}</span> {job.stage}
                    <div className="hint">
                      Created {formatDateTime(job.created_at)} · attempt {job.attempts}/{job.max_attempts}
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
            <Pager page={jobs.data} onOffsetChange={setOffset} />
          </div>
        ))}
    </>
  );
}
