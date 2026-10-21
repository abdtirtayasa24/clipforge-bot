import { type FormEvent, type ReactNode, useState } from "react";
import { useParams } from "react-router";
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
import type { IconName } from "../components/Icon";
import { type IconTone, ListGroup, ListRow } from "../components/ListRow";
import { PageHeader } from "../components/PageHeader";
import { ErrorMessage, Loading } from "../components/QueryState";
import { StatusPill } from "../components/StatusPill";
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
  { key: "clip_min_s", label: "Clip min duration (s)", min: 20, max: 90, step: 1 },
  { key: "clip_max_s", label: "Clip max duration (s)", min: 20, max: 90, step: 1 },
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
  dailyCap: string;
  titleOverlay: boolean;
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
    dailyCap: settings.daily_job_cap === null ? "" : String(settings.daily_job_cap),
    titleOverlay: settings.title_overlay_enabled,
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
  const dailyCap = draft.dailyCap.trim() === "" ? null : Number(draft.dailyCap);
  if (dailyCap !== settings.daily_job_cap) {
    patch.daily_job_cap = dailyCap;
  }
  if (draft.titleOverlay !== settings.title_overlay_enabled) {
    patch.title_overlay_enabled = draft.titleOverlay;
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

function Section({ title, children }: { title?: string; children: ReactNode }) {
  return (
    <section className="card">
      {title && <h2>{title}</h2>}
      {children}
    </section>
  );
}

type FormSection = "clips" | "models" | "downloads" | "discovery" | "notifications";

interface SectionInfo {
  title: string;
  subtitle: string;
  icon: IconName;
  tone: IconTone;
}

const SECTIONS: Record<FormSection | "drive", SectionInfo> = {
  models: { title: "AI providers", subtitle: "Models, prices and quotas", icon: "sparkles", tone: "purple" },
  clips: { title: "Clip generation", subtitle: "Duration, scoring, daily cap and title", icon: "scissors", tone: "blue" },
  discovery: { title: "YouTube discovery", subtitle: "Region, categories and limits", icon: "trending", tone: "red" },
  downloads: { title: "Downloads", subtitle: "Permitted download domains", icon: "download", tone: "green" },
  drive: { title: "Google Drive", subtitle: "Clip storage", icon: "cloud", tone: "blue" },
  notifications: { title: "Notifications", subtitle: "Telegram alerts", icon: "bell", tone: "orange" },
};

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
  section,
  settings,
  providers,
  save,
}: {
  section: FormSection;
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
      <p className="notice">Changes apply to new jobs only; running jobs keep the settings they started with.</p>

      {section === "clips" && (
        <Section>
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
          <div>
            <label>
              <span>Daily job cap (1–100, blank = no limit)</span>
              <input
                type="number"
                min={1}
                max={100}
                step={1}
                value={draft.dailyCap}
                onChange={(event) => setDraft({ ...draft, dailyCap: event.target.value })}
              />
            </label>
            <p className="hint">New jobs beyond the cap wait until a slot frees up within 24 hours.</p>
            <FieldError errors={errors} name="daily_job_cap" />
          </div>
          <label className="switch-row">
            Show title at clip start
            <input
              type="checkbox"
              className="switch"
              checked={draft.titleOverlay}
              onChange={(event) => setDraft({ ...draft, titleOverlay: event.target.checked })}
            />
          </label>
          <p className="hint">
            Burns the clip title into the first 3 seconds, so an early frame works as the cover on YouTube Shorts and
            TikTok. Applies to jobs started after saving.
          </p>
          <FieldError errors={errors} name="" />
        </Section>
      )}

      {section === "models" && (
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
      )}

      {section === "downloads" && (
        <Section>
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
      )}

      {section === "discovery" && (
        <Section>
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
      )}

      {section === "notifications" && (
        <Section>
          <label className="switch-row">
            Telegram notifications
            <input
              type="checkbox"
              className="switch"
              checked={draft.notifications}
              onChange={(event) => setDraft({ ...draft, notifications: event.target.checked })}
            />
          </label>
          <p className="hint">Messages from the bot when clips are ready for review or a job fails.</p>
        </Section>
      )}

      {save.isError && Object.keys(errors).length === 0 && <ErrorMessage error={save.error} />}
      {save.isSuccess && unchanged && <p role="status">Settings saved.</p>}
      <button type="submit" className="block" disabled={save.isPending || unchanged}>
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
      <label className="switch-row">
        Enabled
        <input type="checkbox" className="switch" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} />
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
      <button type="submit" className="block" disabled={save.isPending}>
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
      {status.isPending && <Loading />}
      {status.isError && <ErrorMessage error={status.error} />}
      {status.isSuccess && (
        <>
          <p className="row">
            Status <StatusPill status={status.data.status} />
          </p>
          {status.data.account_email && <p className="hint">Account: {status.data.account_email}</p>}
          <p className="hint">Folder: {status.data.target_folder_name}</p>
          {status.data.status !== "unavailable" && (
            <button type="button" className="block" disabled={connect.isPending} onClick={startConsent}>
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

function onOff(value: boolean): string {
  return value ? "On" : "Off";
}

/** The settings home: a menu of sections, each opening its own screen. */
export default function Settings() {
  const settings = useSettings();
  const drive = useDriveStatus();
  const row = (section: keyof typeof SECTIONS, trailing?: string) => {
    const info = SECTIONS[section];
    return (
      <ListRow
        key={section}
        to={`/settings/${section}`}
        icon={info.icon}
        tone={info.tone}
        title={info.title}
        subtitle={info.subtitle}
        trailing={trailing}
      />
    );
  };

  return (
    <>
      <PageHeader title="Settings" subtitle="Configure ClipForge Bot" />
      {settings.isError && <ErrorMessage error={settings.error} />}
      <ListGroup title="Processing">
        {row("models")}
        {row("clips")}
        {row("discovery", settings.data?.discovery.region_code)}
        {row("downloads", settings.data && String(settings.data.permitted_download_domains.length))}
      </ListGroup>
      <ListGroup title="Integrations">
        <ListRow
          to="/affiliate"
          icon="link"
          tone="purple"
          title="Affiliate links"
          subtitle="Catalog, matching and disclosure"
          trailing={settings.data && onOff(settings.data.affiliate_links_enabled)}
        />
        {row("drive", drive.data?.status)}
      </ListGroup>
      <ListGroup title="General">
        {row("notifications", settings.data && onOff(settings.data.telegram_notifications_enabled))}
      </ListGroup>
    </>
  );
}

function isFormSection(value: string | undefined): value is FormSection {
  return value !== undefined && value !== "drive" && value in SECTIONS;
}

/** One settings section on its own screen; only that section's fields can change and be sent. */
export function SettingsSection() {
  const { section } = useParams();
  const settings = useSettings();
  const providers = useProviders();
  // Owned here: a successful save refetches settings and remounts the form with fresh values.
  const save = usePatchSettings();

  if (section === "drive") {
    return (
      <>
        <PageHeader title={SECTIONS.drive.title} subtitle="Where approved clips are stored" />
        <DriveSection />
      </>
    );
  }
  if (!isFormSection(section)) {
    return <ErrorMessage error={new Error("unknown settings section")} />;
  }
  const info = SECTIONS[section];

  return (
    <>
      <PageHeader title={info.title} subtitle={info.subtitle} />
      {(settings.isPending || providers.isPending) && <Loading />}
      {settings.isError && <ErrorMessage error={settings.error} />}
      {providers.isError && <ErrorMessage error={providers.error} />}
      {settings.isSuccess && providers.isSuccess && (
        <>
          <SettingsForm
            key={JSON.stringify(settings.data)}
            section={section}
            settings={settings.data}
            providers={providers.data}
            save={save}
          />
          {section === "models" && (
            <>
              <h2 className="group-title spaced">Providers</h2>
              {providers.data.map((provider) => (
                <ProviderForm key={provider.id} provider={provider} />
              ))}
            </>
          )}
        </>
      )}
    </>
  );
}
