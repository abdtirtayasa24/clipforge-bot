import { useParams } from "react-router";
import { useJob, useJobAction } from "../api/jobs";
import type { JobDetail as Job } from "../api/types";
import { CostList } from "../components/CostList";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatCost, formatDateTime, formatDuration } from "../format";
import { buildStageTimeline } from "../jobs/timeline";
import { confirmAction } from "../telegram/webApp";

const CANCELLABLE = new Set(["awaiting_source", "queued", "running", "retry_scheduled"]);

function JobActions({ job }: { job: Job }) {
  const retry = useJobAction(job.id, "retry");
  const cancel = useJobAction(job.id, "cancel");
  const busy = retry.isPending || cancel.isPending;
  const actionError = retry.error ?? cancel.error;

  async function onRetry() {
    if (await confirmAction("Retry this job from its last stage?")) {
      cancel.reset();
      retry.mutate();
    }
  }

  async function onCancel() {
    if (await confirmAction("Cancel this job? A running stage stops at its next checkpoint.")) {
      retry.reset();
      cancel.mutate();
    }
  }

  return (
    <div className="row">
      {job.status === "failed" && (
        <button type="button" disabled={busy} onClick={() => void onRetry()}>
          Retry
        </button>
      )}
      {CANCELLABLE.has(job.status) && !job.cancel_requested && (
        <button type="button" className="danger" disabled={busy} onClick={() => void onCancel()}>
          Cancel job
        </button>
      )}
      {job.cancel_requested && job.status === "running" && (
        <span className="hint">Cancellation requested; the worker stops at the next checkpoint.</span>
      )}
      {actionError && <ErrorMessage error={actionError} />}
    </div>
  );
}

export default function JobDetail() {
  const { jobId = "" } = useParams();
  const job = useJob(jobId);

  if (job.isPending) {
    return <Loading />;
  }
  if (job.isError) {
    return <ErrorMessage error={job.error} />;
  }
  const data = job.data;
  const timeline = buildStageTimeline(data.events, data.finished_at);

  return (
    <>
      <h1>
        Job <span className="badge">{data.status}</span>
      </h1>
      <div className="card">
        <dl>
          <dt className="hint">Stage</dt>
          <dd>{data.stage}</dd>
          <dt className="hint">Attempts</dt>
          <dd>
            {data.attempts} / {data.max_attempts}
          </dd>
          <dt className="hint">Created</dt>
          <dd>{formatDateTime(data.created_at)}</dd>
          <dt className="hint">Finished</dt>
          <dd>{formatDateTime(data.finished_at)}</dd>
        </dl>
        {data.last_error && (
          <p className="error">
            <strong>Last error: </strong>
            {data.last_error}
          </p>
        )}
        <JobActions job={data} />
      </div>

      <section className="card">
        <h2>Timeline</h2>
        <ol className="list" aria-label="Stage timeline">
          {timeline.map((segment) => (
            <li key={`${segment.stage}-${segment.startedAt}`}>
              <div className="row">
                <strong>{segment.stage}</strong>
                <span className="hint">
                  {segment.durationMs === null ? "in progress" : formatDuration(segment.durationMs)}
                </span>
              </div>
              <ul className="hint">
                {segment.events.map((event) => (
                  <li key={event.id}>
                    {formatDateTime(event.created_at)} · {event.event_type}
                    {event.attempt !== null && event.attempt > 0 && ` · attempt ${event.attempt}`}
                    {event.message && ` · ${event.message}`}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ol>
      </section>

      <section className="card" aria-label="Estimated AI cost">
        <h2>Estimated AI cost</h2>
        <CostList costs={data.costs} />
        {data.costs_by_stage.length > 0 && (
          <table>
            <caption className="hint">By stage</caption>
            <tbody>
              {data.costs_by_stage.map((row) => (
                <tr key={`${row.stage ?? "none"}/${row.currency}`}>
                  <td>{row.stage ?? "unassigned"}</td>
                  <td>{formatCost(row)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}
