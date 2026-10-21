import { Link } from "react-router";
import { useDashboard } from "../api/jobs";
import type { CostWindow } from "../api/types";
import { CostList } from "../components/CostList";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatBytes, formatCost, formatDateTime } from "../format";

function CostBreakdown({ title, costWindow }: { title: string; costWindow: CostWindow }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <CostList costs={costWindow.totals} />
      {costWindow.by_model.length > 0 && (
        <table>
          <caption className="hint">By provider / model</caption>
          <tbody>
            {costWindow.by_model.map((row) => (
              <tr key={`${row.provider}/${row.model}/${row.currency}`}>
                <td>
                  {row.provider} / {row.model}
                </td>
                <td>{formatCost(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {costWindow.by_stage.length > 0 && (
        <table>
          <caption className="hint">By stage</caption>
          <tbody>
            {costWindow.by_stage.map((row) => (
              <tr key={`${row.stage ?? "none"}/${row.currency}`}>
                <td>{row.stage ?? "outside a job"}</td>
                <td>{formatCost(row)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

export default function Dashboard() {
  const dashboard = useDashboard();

  if (dashboard.isPending) {
    return <Loading />;
  }
  if (dashboard.isError) {
    return <ErrorMessage error={dashboard.error} />;
  }
  const data = dashboard.data;
  const jobCounts = Object.entries(data.jobs_by_status);

  return (
    <>
      <h1>Dashboard</h1>
      <div className="stats">
        <Link className="card" to="/clips">
          <div className="hint">Clips awaiting review</div>
          <strong>{data.clips_awaiting_review}</strong>
        </Link>
        <div className="card">
          <div className="hint">Last discovery run</div>
          <strong>{formatDateTime(data.last_discovery_run_at)}</strong>
        </div>
        <div className="card">
          <div className="hint">Disk free</div>
          <strong>{data.disk_free_bytes === null ? "—" : formatBytes(data.disk_free_bytes)}</strong>
        </div>
      </div>

      <section className="card" aria-label="Jobs by status">
        <h2>Jobs</h2>
        {jobCounts.length === 0 ? (
          <p className="hint">No jobs yet.</p>
        ) : (
          <div className="row">
            {jobCounts.map(([status, count]) => (
              <Link key={status} to={`/jobs?status=${status}`} className="badge">
                {status}: {count}
              </Link>
            ))}
          </div>
        )}
      </section>

      <section aria-label="Estimated AI cost">
        <h2>Estimated AI cost</h2>
        <p className="hint">Estimates from configured provider prices, not invoices.</p>
        <CostBreakdown title="Last 7 days" costWindow={data.costs.last_7_days} />
        <CostBreakdown title="Last 30 days" costWindow={data.costs.last_30_days} />
        <div className="card">
          <h3>Average per completed job</h3>
          <CostList costs={data.costs.average_per_completed_job} />
          <div className="hint">{data.costs.completed_jobs} completed jobs in the last 30 days</div>
        </div>
      </section>
    </>
  );
}
