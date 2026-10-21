import { QueryClient, QueryClientProvider, useQueryClient } from "@tanstack/react-query";
import { type ReactNode, useEffect, useState } from "react";
import { Link, Route, Routes, useLocation } from "react-router";
import { ApiError } from "./api/client";
import { uploadKeys } from "./api/uploads";
import { useInvalidateVideoData } from "./api/videos";
import { useTelegramAuth } from "./auth/telegram";
import Affiliate from "./pages/Affiliate";
import AffiliateProductForm from "./pages/AffiliateProductForm";
import ClipDetail from "./pages/ClipDetail";
import Clips from "./pages/Clips";
import Dashboard from "./pages/Dashboard";
import JobDetail from "./pages/JobDetail";
import Settings, { SettingsSection } from "./pages/Settings";
import Jobs from "./pages/Jobs";
import Sources from "./pages/Sources";
import Uploads from "./pages/Uploads";
import VideoDetail from "./pages/VideoDetail";
import Videos from "./pages/Videos";
import { Icon, type IconName } from "./components/Icon";
import { useTelegramBackButton } from "./telegram/webApp";
import { uploadQueue } from "./upload/uploadQueue";

/** Bottom tabs; `prefixes` keeps a tab highlighted on the screens pushed from it. */
const NAV_ITEMS: { to: string; label: string; icon: IconName; prefixes: string[] }[] = [
  { to: "/", label: "Home", icon: "home", prefixes: [] },
  { to: "/sources", label: "Sources", icon: "sources", prefixes: ["/sources", "/videos", "/uploads"] },
  { to: "/jobs", label: "Jobs", icon: "jobs", prefixes: ["/jobs"] },
  { to: "/clips", label: "Clips", icon: "clips", prefixes: ["/clips"] },
  { to: "/settings", label: "Settings", icon: "settings", prefixes: ["/settings", "/affiliate"] },
];

function isTabActive(item: (typeof NAV_ITEMS)[number], pathname: string): boolean {
  return item.to === "/" ? pathname === "/" : item.prefixes.some((prefix) => pathname.startsWith(prefix));
}

function BottomNav() {
  const { pathname } = useLocation();
  return (
    <nav className="tab-bar" aria-label="Main">
      {NAV_ITEMS.map((item) => {
        const active = isTabActive(item, pathname);
        return (
          <Link key={item.to} to={item.to} className={active ? "active" : undefined} aria-current={active ? "page" : undefined}>
            <Icon name={item.icon} size={24} />
            <span>{item.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        refetchOnWindowFocus: false,
        retry: (failureCount, error) =>
          !(error instanceof ApiError && error.status < 500) && failureCount < 2,
      },
    },
  });
}

function GateScreen({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="gate">
      <h1>{title}</h1>
      {children}
    </main>
  );
}

/** A finished upload changes the video's source status, its jobs and the active upload. */
function useRefreshOnUploadComplete() {
  const invalidateVideoData = useInvalidateVideoData();
  const queryClient = useQueryClient();
  useEffect(
    () =>
      uploadQueue.onCompleted(() => {
        invalidateVideoData();
        void queryClient.invalidateQueries({ queryKey: uploadKeys.active });
      }),
    [invalidateVideoData, queryClient],
  );
}

function AppShell() {
  useTelegramBackButton();
  useRefreshOnUploadComplete();
  return (
    <>
      <main className="page">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/clips" element={<Clips />} />
          <Route path="/clips/:clipId" element={<ClipDetail />} />
          <Route path="/jobs" element={<Jobs />} />
          <Route path="/jobs/:jobId" element={<JobDetail />} />
          <Route path="/videos" element={<Videos />} />
          <Route path="/videos/:videoId" element={<VideoDetail />} />
          <Route path="/sources" element={<Sources />} />
          <Route path="/uploads" element={<Uploads />} />
          <Route path="/affiliate" element={<Affiliate />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/settings/:section" element={<SettingsSection />} />
          <Route path="/affiliate/products/new" element={<AffiliateProductForm />} />
          <Route path="/affiliate/products/:productId" element={<AffiliateProductForm />} />
        </Routes>
      </main>
      <BottomNav />
    </>
  );
}

export default function App() {
  const auth = useTelegramAuth();
  const [queryClient] = useState(createQueryClient);

  switch (auth.status) {
    case "outside_telegram":
      return (
        <GateScreen title="Open from Telegram">
          <p>ClipForge runs inside Telegram. Open it from the bot's menu button.</p>
        </GateScreen>
      );
    case "authenticating":
      return (
        <GateScreen title="Signing in…">
          <p role="status">Verifying your Telegram session.</p>
        </GateScreen>
      );
    case "denied":
      return (
        <GateScreen title="Access denied">
          <p>This Telegram account is not the ClipForge administrator.</p>
        </GateScreen>
      );
    case "expired":
      return (
        <GateScreen title="Session expired">
          <p>Close the Mini App and open it again from Telegram.</p>
        </GateScreen>
      );
    case "failed":
      return (
        <GateScreen title="Sign-in failed">
          <p>{auth.message}</p>
          <p>Close the Mini App and open it again from Telegram.</p>
        </GateScreen>
      );
    case "ready":
      return (
        <QueryClientProvider client={queryClient}>
          <AppShell />
        </QueryClientProvider>
      );
  }
}
