import { useDashboard } from "../api/jobs";
import type { CostWindow } from "../api/types";
import { CostList } from "../components/CostList";
import { Icon } from "../components/Icon";
import { ListGroup, ListRow } from "../components/ListRow";
import { PageHeader } from "../components/PageHeader";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatBytes, formatCost, formatDateTime } from "../format";

const ACTIVE_STATUSES = ["queued", "running", "retry_scheduled"];

function CostBreakdown({ title, costWindow }: { title: string; costWindow: CostWindow }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="big-number">
        <CostList costs={costWindow.totals} />
      </div>
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
  const count = (status: string) => data.jobs_by_status[status] ?? 0;
  const active = ACTIVE_STATUSES.reduce((sum, status) => sum + count(status), 0);
  const completed = count("completed") + count("completed_no_clips");

  return (
    <>
      <PageHeader title="Dashboard" subtitle="Your clip automation at a glance" />

      <section className="hero" aria-label="Overview">
        <div className="hero-head">
          <div>
            <h2>Overview</h2>
            <div className="sub">{new Date().toLocaleDateString(undefined, { dateStyle: "medium" })}</div>
          </div>
          <Icon name="sparkles" size={26} />
        </div>
        <div className="hero-stats">
          <div>
            <strong>{data.clips_awaiting_review}</strong>
            <span>To review</span>
          </div>
          <div>
            <strong>{active}</strong>
            <span>Active jobs</span>
          </div>
          <div>
            <strong>{completed}</strong>
            <span>Completed</span>
          </div>
        </div>
      </section>

      <ListGroup label="Needs attention">
        <ListRow
          to="/clips"
          icon="check"
          tone="green"
          title={<span className="big-number">{data.clips_awaiting_review}</span>}
          subtitle="Clips awaiting review"
        />
        <ListRow
          to="/jobs"
          icon="play"
          tone="blue"
          title={<span className="big-number">{active}</span>}
          subtitle="Processing jobs"
        />
        <ListRow
          to="/jobs?status=failed"
          icon="alert"
          tone="red"
          title={<span className="big-number">{count("failed")}</span>}
          subtitle="Failed jobs"
        />
      </ListGroup>

      <ListGroup title="System">
        <ListRow icon="clock" tone="purple" title="Last discovery run" subtitle={formatDateTime(data.last_discovery_run_at)} />
        <ListRow
          icon="disk"
          tone="gray"
          title="Disk free"
          subtitle={data.disk_free_bytes === null ? "—" : formatBytes(data.disk_free_bytes)}
        />
        <ListRow to="/videos" icon="video" tone="orange" title="Videos" subtitle="Discovered and uploaded videos" />
      </ListGroup>

      <section aria-label="Estimated AI cost">
        <h2 className="group-title">Estimated AI cost</h2>
        <p className="hint section-note">Estimates from configured provider prices, not invoices.</p>
        <CostBreakdown title="Last 7 days" costWindow={data.costs.last_7_days} />
        <CostBreakdown title="Last 30 days" costWindow={data.costs.last_30_days} />
        <div className="card">
          <h3>Average per completed job</h3>
          <div className="big-number">
            <CostList costs={data.costs.average_per_completed_job} />
          </div>
          <div className="hint">{data.costs.completed_jobs} completed jobs in the last 30 days</div>
        </div>
      </section>
    </>
  );
}
