import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import { useCreateSource, useRunDiscovery, useSources, useUpdateSource } from "../api/sources";
import type { SourceKind, SourceOut } from "../api/types";
import { ErrorMessage, Loading } from "../components/QueryState";
import { formatDateTime } from "../format";

function fieldErrors(error: unknown): Record<string, string> {
  return error instanceof ApiError ? error.fieldErrors() : {};
}

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
      <h2>Add source</h2>
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
      <button type="submit" disabled={create.isPending || name.trim() === ""}>
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
    <li>
      <div className="row">
        <strong>{source.name}</strong>
        <span className="badge">{source.kind}</span>
        {source.region_code && <span className="badge">{source.region_code}</span>}
      </div>
      <div className="hint">
        {source.channel_id ?? ""} Last run {formatDateTime(source.last_run_at)} ·{" "}
        <Link to={`/videos?source_id=${source.id}`}>videos</Link>
      </div>
      <div className="row">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={source.enabled}
            disabled={update.isPending}
            onChange={(event) => update.mutate({ id: source.id, body: { enabled: event.target.checked } })}
          />
          Enabled
        </label>
        <button type="button" className="secondary" onClick={() => setEditing(!editing)}>
          {editing ? "Close" : "Edit"}
        </button>
      </div>
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
          <button type="submit" disabled={update.isPending}>
            Save
          </button>
        </form>
      )}
      {update.isError && Object.keys(errors).length === 0 && <ErrorMessage error={update.error} />}
    </li>
  );
}

export default function Sources() {
  const sources = useSources();
  const discovery = useRunDiscovery();

  return (
    <>
      <h1>Sources</h1>
      <div className="card">
        <button type="button" disabled={discovery.isPending} onClick={() => discovery.mutate()}>
          Run discovery now
        </button>
        {discovery.isSuccess && <p role="status">Discovery started. New videos appear in Videos.</p>}
        {discovery.isError && <ErrorMessage error={discovery.error} />}
      </div>

      {sources.isPending && <Loading />}
      {sources.isError && <ErrorMessage error={sources.error} />}
      {sources.isSuccess &&
        (sources.data.length === 0 ? (
          <p className="hint">No sources yet.</p>
        ) : (
          <ul className="list card">
            {sources.data.map((source) => (
              <SourceItem key={source.id} source={source} />
            ))}
          </ul>
        ))}

      <NewSourceForm />
    </>
  );
}
