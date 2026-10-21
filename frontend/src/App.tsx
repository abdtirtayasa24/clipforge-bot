import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { type ReactNode, useState } from "react";
import { NavLink, Route, Routes } from "react-router";
import { ApiError } from "./api/client";
import { useTelegramAuth } from "./auth/telegram";
import ClipDetail from "./pages/ClipDetail";
import Clips from "./pages/Clips";
import Dashboard from "./pages/Dashboard";
import JobDetail from "./pages/JobDetail";
import Jobs from "./pages/Jobs";
import Sources from "./pages/Sources";
import VideoDetail from "./pages/VideoDetail";
import Videos from "./pages/Videos";
import { useTelegramBackButton } from "./telegram/webApp";

const NAV_ITEMS = [
  { to: "/", label: "Dashboard" },
  { to: "/clips", label: "Clips" },
  { to: "/jobs", label: "Jobs" },
  { to: "/videos", label: "Videos" },
  { to: "/sources", label: "Sources" },
];

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

function AppShell() {
  useTelegramBackButton();
  return (
    <>
      <nav className="tabs" aria-label="Main">
        {NAV_ITEMS.map((item) => (
          <NavLink key={item.to} to={item.to} end={item.to === "/"}>
            {item.label}
          </NavLink>
        ))}
      </nav>
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
        </Routes>
      </main>
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
