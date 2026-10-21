import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import { useCreateSource, useRunDiscovery, useSources, useUpdateSource } from "../api/sources";
import type { SourceKind, SourceOut } from "../api/types";
import { Icon } from "../components/Icon";
import { IconTile, ListGroup, ListRow } from "../components/ListRow";
import { PageHeader } from "../components/PageHeader";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../format";

function fieldErrors(error: unknown): Record<string, string> {
  return error instanceof ApiError ? error.fieldErrors() : {};
}

type KindFilter = SourceKind | "all";

const KIND_FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "trending", label: "Trending" },
  { value: "channel", label: "Channels" },
];

function NewSourceForm() {
  const create = useCreateSource();
  const [kind, setKind] = useState<SourceKind>("trending");
  const [name, setName] = useState("");
  const [channelId, setChannelId] = useState("");
  const [region, setRegion] = useState("ID");
  const errors = fieldErrors(create.error);

  function submit(event: FormEvent) {
    event.preventDefault();
    const body =
      kind === "channel"
        ? { kind, name: name.trim(), channel_id: channelId.trim() }
        : { kind, name: name.trim(), region_code: region.trim() || null };
    create.mutate(body, {
      onSuccess: () => {
        setName("");
        setChannelId("");
      },
    });
  }

  return (
    <form className="card" aria-label="New source" onSubmit={submit}>
      <h2>New source</h2>
      <label>
        <span>Kind</span>
        <select value={kind} onChange={(event) => setKind(event.target.value as SourceKind)}>
          <option value="trending">Trending (region chart)</option>
          <option value="channel">Channel</option>
        </select>
      </label>
      <label>
        <span>Name</span>
        <input required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
      </label>
      {errors.name && <p className="error">{errors.name}</p>}
      {kind === "channel" ? (
        <label>
          <span>Channel ID</span>
          <input
            required
            placeholder="UC…"
            value={channelId}
            onChange={(event) => setChannelId(event.target.value)}
          />
        </label>
      ) : (
        <label>
          <span>Region</span>
          <input maxLength={2} value={region} onChange={(event) => setRegion(event.target.value)} />
        </label>
      )}
      {errors.channel_id && <p className="error">{errors.channel_id}</p>}
      {errors.region_code && <p className="error">{errors.region_code}</p>}
      {create.isError && Object.keys(errors).length === 0 && <ErrorMessage error={create.error} />}
      <button type="submit" className="block" disabled={create.isPending || name.trim() === ""}>
        Add source
      </button>
    </form>
  );
}

function SourceItem({ source }: { source: SourceOut }) {
  const update = useUpdateSource();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(source.name);
  const [region, setRegion] = useState(source.region_code ?? "");
  const errors = fieldErrors(update.error);

  function save(event: FormEvent) {
    event.preventDefault();
    const body = source.kind === "trending" ? { name: name.trim(), region_code: region.trim() } : { name: name.trim() };
    update.mutate({ id: source.id, body }, { onSuccess: () => setEditing(false) });
  }

  return (
    <li className="list-row">
      <div className="row-body">
        <IconTile name={source.kind === "trending" ? "trending" : "channel"} tone={source.enabled ? "blue" : "gray"} />
        <span className="row-text">
          <span className="row-title">{source.name}</span>
          <span className="row-subtitle">
            {source.kind === "trending" ? `Trending · ${source.region_code ?? "—"}` : `Channel · ${source.channel_id ?? ""}`}
          </span>
          <span className="row-subtitle">
            Last run {formatDateTime(source.last_run_at)} · <Link to={`/videos?source_id=${source.id}`}>Videos</Link>
          </span>
        </span>
        <span className="row-trailing">
          <input
            type="checkbox"
            className="switch"
            aria-label="Enabled"
            checked={source.enabled}
            disabled={update.isPending}
            onChange={(event) => update.mutate({ id: source.id, body: { enabled: event.target.checked } })}
          />
          <button type="button" className="secondary small" aria-expanded={editing} onClick={() => setEditing(!editing)}>
            {editing ? "Close" : "Edit"}
          </button>
        </span>
      </div>
      <div className="row-form">
        {editing && (
          <form aria-label={`Edit ${source.name}`} onSubmit={save}>
            <label>
              <span>Name</span>
              <input required maxLength={200} value={name} onChange={(event) => setName(event.target.value)} />
            </label>
            {errors.name && <p className="error">{errors.name}</p>}
            {source.kind === "trending" && (
              <label>
                <span>Region</span>
                <input maxLength={2} value={region} onChange={(event) => setRegion(event.target.value)} />
              </label>
            )}
            {errors.region_code && <p className="error">{errors.region_code}</p>}
            <button type="submit" className="block" disabled={update.isPending}>
              Save
            </button>
          </form>
        )}
        {update.isError && Object.keys(errors).length === 0 && <ErrorMessage error={update.error} />}
      </div>
    </li>
  );
}

export default function Sources() {
  const sources = useSources();
  const discovery = useRunDiscovery();
  const [kind, setKind] = useState<KindFilter>("all");
  const [adding, setAdding] = useState(false);
  const shown = sources.data?.filter((source) => kind === "all" || source.kind === kind) ?? [];

  return (
    <>
      <PageHeader title="Sources" subtitle="Manage your YouTube sources" />
      <div className="segmented" role="group" aria-label="Source kind">
        {KIND_FILTERS.map((filter) => (
          <button key={filter.value} type="button" aria-pressed={filter.value === kind} onClick={() => setKind(filter.value)}>
            {filter.label}
          </button>
        ))}
      </div>

      <div className="actions">
        <button type="button" className="with-icon" aria-expanded={adding} onClick={() => setAdding(!adding)}>
          <Icon name="plus" size={18} />
          New source
        </button>
        <button
          type="button"
          className="secondary with-icon"
          disabled={discovery.isPending}
          onClick={() => discovery.mutate()}
        >
          <Icon name="refresh" size={18} />
          Run discovery now
        </button>
      </div>
      {discovery.isSuccess && (
        <p role="status" className="notice">
          Discovery started. New videos appear in Videos.
        </p>
      )}
      {discovery.isError && <ErrorMessage error={discovery.error} />}
      {adding && <NewSourceForm />}

      {sources.isPending && <Loading />}
      {sources.isError && <ErrorMessage error={sources.error} />}
      {sources.isSuccess &&
        (shown.length === 0 ? (
          <p className="empty">{sources.data.length === 0 ? "No sources yet." : "No sources of this kind."}</p>
        ) : (
          <ul className="group-list" aria-label="Sources">
            {shown.map((source) => (
              <SourceItem key={source.id} source={source} />
            ))}
          </ul>
        ))}

      <ListGroup label="Library">
        <ListRow to="/videos" icon="video" tone="orange" title="All videos" subtitle="Everything discovered or uploaded" />
      </ListGroup>
    </>
  );
}
