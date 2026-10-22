import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { AdminSettings, DriveStatus, ProviderOut, SettingsPatch } from "../api/types";
import { apiError, mockApi } from "../test/fetchMock";
import { settingsFixture } from "../test/fixtures";
import { renderPage } from "../test/render";
import { installTelegram, removeTelegram } from "../test/telegramStub";
import Settings, { SettingsSection } from "./Settings";

function provider(overrides: Partial<ProviderOut> = {}): ProviderOut {
  return {
    id: "pa",
    provider: "gemini",
    model: "gemini-3.5-flash-lite",
    capability: "analysis",
    enabled: true,
    selected_for: ["analysis"],
    secret_configured: true,
    prices: { input_per_mtok: "0.1", output_per_mtok: "0.4", per_audio_minute: "0", currency: "USD" },
    quota: { rpm_limit: 15, rpd_limit: 1000, reset: "pacific_midnight", used_today: 42 },
    ...overrides,
  };
}

const PROVIDERS = [
  provider(),
  provider({ id: "pb", provider: "openrouter", model: "free-llm", selected_for: [] }),
  provider({ id: "pc", provider: "openrouter", model: "disabled-llm", enabled: false, selected_for: [] }),
  provider({ id: "pt", model: "gemini-3.5-transcribe", capability: "transcription", selected_for: ["transcription"] }),
];

const DRIVE: DriveStatus = {
  status: "disconnected",
  scopes: [],
  account_email: null,
  target_folder_name: "ClipForge",
  target_folder_id: null,
};

function server(patchReply?: (body: unknown) => { status?: number; body?: unknown }) {
  let settings: AdminSettings = settingsFixture();
  return mockApi({
    "GET /api/v1/settings": () => ({ body: settings }),
    "PATCH /api/v1/settings": (call) => {
      if (patchReply) {
        return patchReply(call.body);
      }
      // Like the API: only sent keys change, nested sections are merged, the full settings come back.
      const patch = call.body as SettingsPatch;
      settings = {
        ...settings,
        ...patch,
        discovery: { ...settings.discovery, ...patch.discovery },
        models: { ...settings.models, ...patch.models },
      } as AdminSettings;
      return { body: settings };
    },
    "GET /api/v1/ai/providers": { body: { items: PROVIDERS } },
    "GET /api/v1/integrations/drive/status": { body: DRIVE },
    "GET /api/v1/integrations/drive/connect": { body: { auth_url: "https://accounts.google.com/o/oauth2/auth?x=1" } },
  });
}

afterEach(() => {
  removeTelegram();
});

function renderSection(section: string) {
  return renderPage(<SettingsSection />, { route: `/settings/${section}`, path: "/settings/:section" });
}

describe("Settings menu", () => {
  it("links every section and shows current values", async () => {
    server();
    renderPage(<Settings />);

    const clips = await screen.findByRole("link", { name: /Clip generation/ });
    expect(clips).toHaveAttribute("href", "/settings/clips");
    expect(screen.getByRole("link", { name: /AI providers/ })).toHaveAttribute("href", "/settings/models");
    expect(screen.getByRole("link", { name: /YouTube discovery/ })).toHaveAttribute("href", "/settings/discovery");
    expect(screen.getByRole("link", { name: /Downloads/ })).toHaveAttribute("href", "/settings/downloads");
    expect(screen.getByRole("link", { name: /Google Drive/ })).toHaveAttribute("href", "/settings/drive");
    expect(screen.getByRole("link", { name: /Affiliate links/ })).toHaveAttribute("href", "/affiliate");
    expect(await screen.findByRole("link", { name: /Notifications.*On/ })).toHaveAttribute(
      "href",
      "/settings/notifications",
    );
  });
});

describe("Settings sections", () => {
  it("sends only the changed keys and says changes apply to new jobs", async () => {
    const calls = server();
    renderSection("clips");

    const minScore = await screen.findByLabelText("Minimum score (0–100)");
    expect(screen.getByText(/apply to new jobs only/)).toBeInTheDocument();
    await userEvent.clear(minScore);
    await userEvent.type(minScore, "80");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ min_score: 80 });
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
  });

  it("sets and clears the daily job cap", async () => {
    const calls = server();
    renderSection("clips");

    const cap = await screen.findByLabelText("Daily job cap (1–100, blank = no limit)");
    expect(cap).toHaveValue(null);
    await userEvent.type(cap, "10");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));
    expect(await screen.findByText("Settings saved.")).toBeInTheDocument();
    // The form re-renders from the saved settings, so look the field up again.
    await userEvent.clear(screen.getByLabelText("Daily job cap (1–100, blank = no limit)"));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    const patches = calls.filter((c) => c.method === "PATCH").map((c) => c.body);
    expect(patches).toEqual([{ daily_job_cap: 10 }, { daily_job_cap: null }]);
  });

  it("turns the clip title overlay off", async () => {
    const calls = server();
    renderSection("clips");

    const overlay = await screen.findByLabelText("Show title at clip start");
    expect(overlay).toBeChecked();
    await userEvent.click(overlay);
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ title_overlay_enabled: false });
  });

  it("turns subject reframing on", async () => {
    const calls = server();
    renderSection("clips");

    const reframing = await screen.findByLabelText("Keep people in frame");
    expect(reframing).not.toBeChecked();
    await userEvent.click(reframing);
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ subject_reframing_enabled: true });
  });

  it("turns Telegram notifications off", async () => {
    const calls = server();
    renderSection("notifications");

    await userEvent.click(await screen.findByLabelText("Telegram notifications"));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ telegram_notifications_enabled: false });
  });

  it("bounds inputs to the API limits", async () => {
    server();
    renderSection("clips");

    const maxClips = await screen.findByLabelText("Max clips per video (1–3)");
    expect(maxClips).toHaveAttribute("min", "1");
    expect(maxClips).toHaveAttribute("max", "3");
    expect(screen.getByLabelText("Max overlap (0–0.2)")).toHaveAttribute("max", "0.2");
    expect(screen.getByLabelText("Clip min duration (s)")).toHaveAttribute("min", "20");
    expect(screen.getByLabelText("Clip max duration (s)")).toHaveAttribute("max", "90");
  });

  it("shows API validation errors inline", async () => {
    const calls = server(() =>
      apiError(422, "validation_error", "settings validation failed", [
        { loc: ["body", "clip_max_s"], msg: "clip_min_s must not exceed clip_max_s", type: "value_error" },
      ]),
    );
    renderSection("clips");

    const minDuration = await screen.findByLabelText("Clip min duration (s)");
    await userEvent.clear(minDuration);
    await userEvent.type(minDuration, "59");
    const maxDuration = screen.getByLabelText("Clip max duration (s)");
    await userEvent.clear(maxDuration);
    await userEvent.type(maxDuration, "30");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(await screen.findByText("clip_min_s must not exceed clip_max_s")).toBeInTheDocument();
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ clip_min_s: 59, clip_max_s: 30 });
  });

  it("offers only enabled models of the matching capability", async () => {
    const calls = server();
    renderSection("models");

    const analysis = await screen.findByLabelText("Analysis model");
    const options = within(analysis).getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["gemini / gemini-3.5-flash-lite", "openrouter / free-llm"]);

    await userEvent.selectOptions(analysis, "openrouter:free-llm");
    await userEvent.selectOptions(screen.getByLabelText("Analysis fallback model"), "");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ models: { analysis: "openrouter:free-llm" } });
  });

  it("edits provider prices and quota limits with today's usage", async () => {
    const calls = server();
    renderSection("models");

    const card = (await screen.findByText("gemini / gemini-3.5-flash-lite", { selector: "h3" })).closest("form");
    const scope = within(card as HTMLElement);
    expect(scope.getByText(/42 requests today/)).toBeInTheDocument();
    await userEvent.clear(scope.getByLabelText("Requests per day"));
    await userEvent.clear(scope.getByLabelText("Input price per 1M tokens"));
    await userEvent.type(scope.getByLabelText("Input price per 1M tokens"), "0.15");
    await userEvent.click(scope.getByRole("button", { name: "Save provider" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      providers: [{ id: "pa", rpd_limit: null, price_input_per_mtok: "0.15" }],
    });
  });

  it("edits the permitted download domains", async () => {
    const calls = server();
    renderSection("downloads");

    const domains = await screen.findByLabelText("Permitted download domains (one per line)");
    await userEvent.type(domains, "\n*.example.org");
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
      permitted_download_domains: ["cdn.example.com", "*.example.org"],
    });
  });

  it("excludes discovery categories by name", async () => {
    const calls = server();
    renderSection("discovery");

    await userEvent.click(await screen.findByRole("checkbox", { name: "Music" }));
    await userEvent.click(screen.getByRole("button", { name: "Save settings" }));

    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ discovery: { excluded_category_ids: ["10"] } });
  });

  it("shows Drive status and opens the OAuth consent externally", async () => {
    const webApp = installTelegram();
    server();
    renderSection("drive");

    const drive = await screen.findByRole("region", { name: "Google Drive" });
    expect(await within(drive).findByText("disconnected")).toBeInTheDocument();
    await userEvent.click(within(drive).getByRole("button", { name: "Connect Google Drive" }));

    await expect.poll(() => webApp.openLink).toHaveBeenCalledWith("https://accounts.google.com/o/oauth2/auth?x=1");
  });

  it("rejects an unknown section", () => {
    server();
    renderSection("nope");
    expect(screen.getByRole("alert")).toHaveTextContent("unknown settings section");
  });
});
