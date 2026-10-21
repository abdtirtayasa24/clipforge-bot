import { type FormEvent, type ReactNode, useState } from "react";
import { Link } from "react-router";
import { ApiError } from "../api/client";
import {
  useDriveConnect,
  useDriveStatus,
  usePatchProvider,
  usePatchSettings,
  useProviders,
  useSettings,
} from "../api/settings";
import type { AdminSettings, ModelSelection, ProviderOut, ProviderPatch, SettingsPatch } from "../api/types";
import { ErrorMessage, Loading } from "../components/QueryState";
import { openExternalLink } from "../telegram/webApp";

/** YouTube's fixed video category IDs, so exclusions can be picked by name. */
const YOUTUBE_CATEGORIES: [string, string][] = [
  ["1", "Film & Animation"],
  ["2", "Autos & Vehicles"],
  ["10", "Music"],
  ["15", "Pets & Animals"],
  ["17", "Sports"],
  ["19", "Travel & Events"],
  ["20", "Gaming"],
  ["22", "People & Blogs"],
  ["23", "Comedy"],
  ["24", "Entertainment"],
  ["25", "News & Politics"],
  ["26", "Howto & Style"],
  ["27", "Education"],
  ["28", "Science & Technology"],
  ["29", "Nonprofits & Activism"],
];

const MODEL_ROLES: { role: keyof ModelSelection; label: string; optional?: boolean }[] = [
  { role: "transcription", label: "Transcription model" },
  { role: "analysis", label: "Analysis model" },
  { role: "analysis_fallback", label: "Analysis fallback model", optional: true },
  { role: "rerank", label: "Rerank model" },
  { role: "embedding", label: "Embedding model" },
];

interface NumberField {
  key: "min_score" | "clip_min_s" | "clip_max_s" | "max_clips" | "max_overlap";
  label: string;
  min: number;
  max: number;
  step: number;
}

const CLIP_FIELDS: NumberField[] = [
  { key: "min_score", label: "Minimum score (0–100)", min: 0, max: 100, step: 1 },
  { key: "clip_min_s", label: "Clip min duration (s)", min: 20, max: 60, step: 1 },
  { key: "clip_max_s", label: "Clip max duration (s)", min: 20, max: 60, step: 1 },
  { key: "max_clips", label: "Max clips per video (1–3)", min: 1, max: 3, step: 1 },
  { key: "max_overlap", label: "Max overlap (0–0.2)", min: 0, max: 0.2, step: 0.01 },
];

type DiscoveryNumberKey = "top_categories" | "chart_pages" | "per_category_results" | "max_new_eligible_per_run";

const DISCOVERY_FIELDS: { key: DiscoveryNumberKey; label: string; min: number; max: number }[] = [
  { key: "top_categories", label: "Top categories (1–20)", min: 1, max: 20 },
  { key: "chart_pages", label: "Chart pages (1–4)", min: 1, max: 4 },
  { key: "per_category_results", label: "Results per category (1–50)", min: 1, max: 50 },
  { key: "max_new_eligible_per_run", label: "Max new videos per run (1–50)", min: 1, max: 50 },
];

interface Draft {
  numbers: Record<NumberField["key"], string>;
  notifications: boolean;
  domains: string;
  region: string;
  discovery: Record<DiscoveryNumberKey, string>;
  excluded: string[];
  models: ModelSelection;
}

function toDraft(settings: AdminSettings): Draft {
  return {
    numbers: {
      min_score: String(settings.min_score),
      clip_min_s: String(settings.clip_min_s),
      clip_max_s: String(settings.clip_max_s),
      max_clips: String(settings.max_clips),
      max_overlap: String(settings.max_overlap),
    },
    notifications: settings.telegram_notifications_enabled,
    domains: settings.permitted_download_domains.join("\n"),
    region: settings.discovery.region_code,
    discovery: {
      top_categories: String(settings.discovery.top_categories),
      chart_pages: String(settings.discovery.chart_pages),
      per_category_results: String(settings.discovery.per_category_results),
      max_new_eligible_per_run: String(settings.discovery.max_new_eligible_per_run),
    },
    excluded: settings.discovery.excluded_category_ids,
    models: settings.models,
  };
}

const sameList = (a: string[], b: string[]) => a.length === b.length && a.every((value, index) => value === b[index]);

/** Only changed keys are sent: each one becomes an audited settings change on the server. */
function buildPatch(settings: AdminSettings, draft: Draft): SettingsPatch {
  const patch: SettingsPatch = {};
  for (const field of CLIP_FIELDS) {
    const value = Number(draft.numbers[field.key]);
    if (value !== settings[field.key]) {
      patch[field.key] = value;
    }
  }
  if (draft.notifications !== settings.telegram_notifications_enabled) {
    patch.telegram_notifications_enabled = draft.notifications;
  }
  const domains = draft.domains
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  if (!sameList(domains, settings.permitted_download_domains)) {
    patch.permitted_download_domains = domains;
  }

  const discovery: SettingsPatch["discovery"] = {};
  const region = draft.region.trim().toUpperCase();
  if (region !== settings.discovery.region_code) {
    discovery.region_code = region;
  }
  for (const field of DISCOVERY_FIELDS) {
    const value = Number(draft.discovery[field.key]);
    if (value !== settings.discovery[field.key]) {
      discovery[field.key] = value;
    }
  }
  if (!sameList([...draft.excluded].sort(), [...settings.discovery.excluded_category_ids].sort())) {
    discovery.excluded_category_ids = draft.excluded;
  }
  if (Object.keys(discovery).length > 0) {
    patch.discovery = discovery;
  }

  const models: SettingsPatch["models"] = {};
  for (const { role } of MODEL_ROLES) {
    if (draft.models[role] !== settings.models[role]) {
      Object.assign(models, { [role]: draft.models[role] });
    }
  }
  if (Object.keys(models).length > 0) {
    patch.models = models;
  }
  return patch;
}

function FieldError({ errors, name }: { errors: Record<string, string>; name: string }) {
  return errors[name] ? <p className="error">{errors[name]}</p> : null;
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="card">
      <h2>{title}</h2>
      {children}
    </section>
  );
}

const selectionValue = (provider: ProviderOut) => `${provider.provider}:${provider.model}`;

/** Settings may hold a bare model ID or `provider:model`; both select the same option. */
function selectedOption(current: string | null, options: ProviderOut[]): string {
  if (current === null) {
    return "";
  }
  const match = options.find((option) => selectionValue(option) === current || option.model === current);
  return match ? selectionValue(match) : current;
}

function ModelSelect({
  role,
  label,
  optional,
  value,
  providers,
  onChange,
}: {
  role: keyof ModelSelection;
  label: string;
  optional?: boolean;
  value: string | null;
  providers: ProviderOut[];
  onChange: (value: string | null) => void;
}) {
  const capability = role.replace(/_fallback$/, "");
  const options = providers.filter((provider) => provider.enabled && provider.capability === capability);
  const selected = selectedOption(value, options);
  const known = selected === "" || options.some((option) => selectionValue(option) === selected);

  return (
    <label>
      <span>{label}</span>
      <select
        value={selected}
        onChange={(event) => onChange(event.target.value === "" ? null : event.target.value)}
      >
        {optional && <option value="">None</option>}
        {!known && <option value={selected}>{selected} (not enabled)</option>}
        {options.map((option) => (
          <option key={option.id} value={selectionValue(option)}>
            {option.provider} / {option.model}
          </option>
        ))}
      </select>
    </label>
  );
}

function SettingsForm({
  settings,
  providers,
  save,
}: {
  settings: AdminSettings;
  providers: ProviderOut[];
  save: ReturnType<typeof usePatchSettings>;
}) {
  const [draft, setDraft] = useState(() => toDraft(settings));
  const errors = save.error instanceof ApiError ? save.error.fieldErrors() : {};
  const patch = buildPatch(settings, draft);
  const unchanged = Object.keys(patch).length === 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!unchanged) {
      save.mutate(patch);
    }
  }

  function toggleExcluded(id: string, checked: boolean) {
    setDraft({
      ...draft,
      excluded: checked ? [...draft.excluded, id] : draft.excluded.filter((value) => value !== id),
    });
  }

  return (
    <form aria-label="Settings" onSubmit={submit}>
      <p className="hint">Changes apply to new jobs only; running jobs keep the settings they started with.</p>

      <Section title="Clip selection">
        {CLIP_FIELDS.map((field) => (
          <div key={field.key}>
            <label>
              <span>{field.label}</span>
              <input
                type="number"
                required
                min={field.min}
                max={field.max}
                step={field.step}
                value={draft.numbers[field.key]}
                onChange={(event) =>
                  setDraft({ ...draft, numbers: { ...draft.numbers, [field.key]: event.target.value } })
                }
              />
            </label>
            <FieldError errors={errors} name={field.key} />
          </div>
        ))}
        <FieldError errors={errors} name="" />
      </Section>

      <Section title="AI models">
        <p className="hint">Only enabled models are offered. Prices and quotas are edited per provider below.</p>
        {MODEL_ROLES.map(({ role, label, optional }) => (
          <ModelSelect
            key={role}
            role={role}
            label={label}
            optional={optional}
            value={draft.models[role]}
            providers={providers}
            onChange={(value) => setDraft({ ...draft, models: { ...draft.models, [role]: value } })}
          />
        ))}
        <FieldError errors={errors} name="models" />
      </Section>

      <Section title="Downloads">
        <label>
          <span>Permitted download domains (one per line)</span>
          <textarea
            rows={4}
            value={draft.domains}
            onChange={(event) => setDraft({ ...draft, domains: event.target.value })}
          />
        </label>
        <p className="hint">Hostnames or *.suffix; YouTube and Google video hosts are always refused.</p>
        {Object.entries(errors)
          .filter(([key]) => key.startsWith("permitted_download_domains"))
          .map(([key, message]) => (
            <p key={key} className="error">
              {message}
            </p>
          ))}
      </Section>

      <Section title="Discovery">
        <label>
          <span>Region (ISO code)</span>
          <input maxLength={2} value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })} />
        </label>
        <FieldError errors={errors} name="discovery.region_code" />
        {DISCOVERY_FIELDS.map((field) => (
          <div key={field.key}>
            <label>
              <span>{field.label}</span>
              <input
                type="number"
                required
                min={field.min}
                max={field.max}
                value={draft.discovery[field.key]}
                onChange={(event) =>
                  setDraft({ ...draft, discovery: { ...draft.discovery, [field.key]: event.target.value } })
                }
              />
            </label>
            <FieldError errors={errors} name={`discovery.${field.key}`} />
          </div>
        ))}
        <fieldset>
          <legend>Excluded categories</legend>
          {YOUTUBE_CATEGORIES.map(([id, name]) => (
            <label key={id} className="checkbox">
              <input
                type="checkbox"
                checked={draft.excluded.includes(id)}
                onChange={(event) => toggleExcluded(id, event.target.checked)}
              />
              {name}
            </label>
          ))}
        </fieldset>
      </Section>

      <Section title="Notifications">
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.notifications}
            onChange={(event) => setDraft({ ...draft, notifications: event.target.checked })}
          />
          Telegram notifications
        </label>
        <p className="hint">
          Affiliate settings live on the <Link to="/affiliate">Affiliate</Link> screen.
        </p>
      </Section>

      {save.isError && Object.keys(errors).length === 0 && <ErrorMessage error={save.error} />}
      {save.isSuccess && unchanged && <p role="status">Settings saved.</p>}
      <button type="submit" disabled={save.isPending || unchanged}>
        Save settings
      </button>
    </form>
  );
}

const PRICE_FIELDS = [
  { key: "input_per_mtok", patchKey: "price_input_per_mtok", label: "Input price per 1M tokens" },
  { key: "output_per_mtok", patchKey: "price_output_per_mtok", label: "Output price per 1M tokens" },
  { key: "per_audio_minute", patchKey: "price_per_audio_minute", label: "Price per audio minute" },
] as const;

const LIMIT_FIELDS = [
  { key: "rpm_limit", label: "Requests per minute" },
  { key: "rpd_limit", label: "Requests per day" },
] as const;

function ProviderForm({ provider }: { provider: ProviderOut }) {
  const save = usePatchProvider();
  const [enabled, setEnabled] = useState(provider.enabled);
  const [prices, setPrices] = useState<Record<string, string>>({ ...provider.prices });
  const [limits, setLimits] = useState<Record<string, string>>({
    rpm_limit: provider.quota.rpm_limit === null ? "" : String(provider.quota.rpm_limit),
    rpd_limit: provider.quota.rpd_limit === null ? "" : String(provider.quota.rpd_limit),
  });
  const errors = save.error instanceof ApiError ? save.error.fieldErrors() : {};

  function submit(event: FormEvent) {
    event.preventDefault();
    const patch: ProviderPatch = { id: provider.id };
    if (enabled !== provider.enabled) {
      patch.enabled = enabled;
    }
    for (const field of LIMIT_FIELDS) {
      const value = limits[field.key]?.trim() ?? "";
      const next = value === "" ? null : Number(value);
      if (next !== provider.quota[field.key]) {
        patch[field.key] = next;
      }
    }
    for (const field of PRICE_FIELDS) {
      const value = prices[field.key]?.trim() ?? "";
      if (value !== provider.prices[field.key]) {
        patch[field.patchKey] = value;
      }
    }
    const currency = prices.currency?.trim().toUpperCase() ?? "";
    if (currency !== provider.prices.currency) {
      patch.currency = currency;
    }
    save.mutate(patch);
  }

  return (
    <form className="card" aria-label={`${provider.provider} ${provider.model}`} onSubmit={submit}>
      <h3>
        {provider.provider} / {provider.model}
      </h3>
      <div className="row">
        <span className="badge">{provider.capability}</span>
        {provider.selected_for.map((role) => (
          <span key={role} className="badge">
            selected: {role}
          </span>
        ))}
        {!provider.secret_configured && <span className="badge error">API key missing</span>}
      </div>
      <p className="hint">
        {provider.quota.used_today} requests today (resets at {provider.quota.reset.replace("_", " ")})
      </p>
      <label className="checkbox">
        <input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
        Enabled
      </label>
      {LIMIT_FIELDS.map((field) => (
        <label key={field.key}>
          <span>{field.label}</span>
          <input
            type="number"
            min={1}
            placeholder="unlimited"
            aria-label={field.label}
            value={limits[field.key]}
            onChange={(event) => setLimits({ ...limits, [field.key]: event.target.value })}
          />
        </label>
      ))}
      {PRICE_FIELDS.map((field) => (
        <label key={field.key}>
          <span>{field.label}</span>
          <input
            inputMode="decimal"
            aria-label={field.label}
            value={prices[field.key]}
            onChange={(event) => setPrices({ ...prices, [field.key]: event.target.value })}
          />
        </label>
      ))}
      <label>
        <span>Currency</span>
        <input
          maxLength={3}
          value={prices.currency}
          onChange={(event) => setPrices({ ...prices, currency: event.target.value })}
        />
      </label>
      {Object.entries(errors).map(([key, message]) => (
        <p key={key} className="error">
          {message}
        </p>
      ))}
      {save.isError && Object.keys(errors).length === 0 && <ErrorMessage error={save.error} />}
      {save.isSuccess && <p role="status">Provider saved.</p>}
      <button type="submit" disabled={save.isPending}>
        Save provider
      </button>
    </form>
  );
}

function DriveSection() {
  const status = useDriveStatus();
  const connect = useDriveConnect();

  function startConsent() {
    connect.mutate(undefined, { onSuccess: (authUrl) => openExternalLink(authUrl) });
  }

  return (
    <section className="card" aria-label="Google Drive">
      <h2>Google Drive</h2>
      {status.isPending && <Loading />}
      {status.isError && <ErrorMessage error={status.error} />}
      {status.isSuccess && (
        <>
          <p>
            Status: <span className="badge">{status.data.status}</span>
          </p>
          {status.data.account_email && <p className="hint">Account: {status.data.account_email}</p>}
          <p className="hint">Folder: {status.data.target_folder_name}</p>
          {status.data.status !== "unavailable" && (
            <button type="button" disabled={connect.isPending} onClick={startConsent}>
              {status.data.status === "connected" ? "Reconnect Google Drive" : "Connect Google Drive"}
            </button>
          )}
          <p className="hint">Consent opens in the browser; come back and reopen this screen afterwards.</p>
        </>
      )}
      {connect.isError && <ErrorMessage error={connect.error} />}
    </section>
  );
}

export default function Settings() {
  const settings = useSettings();
  const providers = useProviders();
  // Owned here: a successful save refetches settings and remounts the form with fresh values.
  const save = usePatchSettings();

  return (
    <>
      <h1>Settings</h1>
      {(settings.isPending || providers.isPending) && <Loading />}
      {settings.isError && <ErrorMessage error={settings.error} />}
      {providers.isError && <ErrorMessage error={providers.error} />}
      {settings.isSuccess && providers.isSuccess && (
        <>
          <SettingsForm
            key={JSON.stringify(settings.data)}
            settings={settings.data}
            providers={providers.data}
            save={save}
          />
          <h2>AI providers</h2>
          {providers.data.map((provider) => (
            <ProviderForm key={provider.id} provider={provider} />
          ))}
        </>
      )}
      <DriveSection />
    </>
  );
}
