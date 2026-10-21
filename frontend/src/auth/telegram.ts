import { useEffect, useState } from "react";
import { ApiError, api, setAccessToken, setUnauthorizedHandler } from "../api/client";
import type { SessionResponse } from "../api/types";
import { telegramWebApp } from "../telegram/webApp";

export type AuthState =
  | { status: "outside_telegram" }
  | { status: "authenticating" }
  | { status: "ready" }
  | { status: "denied" }
  | { status: "expired" }
  | { status: "failed"; message: string };

/** Signed initData, or null when the page was not launched from Telegram. */
export function getInitData(): string | null {
  const initData = telegramWebApp()?.initData;
  return initData ? initData : null;
}

export async function authenticate(initData: string): Promise<AuthState> {
  try {
    const session = await api.post<SessionResponse>("/auth/telegram", { init_data: initData });
    setAccessToken(session.access_token);
    return { status: "ready" };
  } catch (error) {
    if (error instanceof ApiError && error.status === 403) {
      return { status: "denied" };
    }
    const message = error instanceof Error ? error.message : "Unknown error";
    return { status: "failed", message };
  }
}

export function useTelegramAuth(): AuthState {
  const [initData] = useState(getInitData);
  const [state, setState] = useState<AuthState>(
    initData === null ? { status: "outside_telegram" } : { status: "authenticating" },
  );

  useEffect(() => {
    if (initData === null) {
      return;
    }
    const webApp = telegramWebApp();
    webApp?.ready();
    webApp?.expand();

    let active = true;
    void authenticate(initData).then((result) => {
      if (!active) {
        return;
      }
      if (result.status === "ready") {
        setUnauthorizedHandler(() => {
          setAccessToken(null);
          setState({ status: "expired" });
        });
      }
      setState(result);
    });
    return () => {
      active = false;
      setUnauthorizedHandler(null);
    };
  }, [initData]);

  return state;
}
