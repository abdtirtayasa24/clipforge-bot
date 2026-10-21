import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router";

interface TelegramButton {
  isVisible: boolean;
  show(): void;
  hide(): void;
  onClick(callback: () => void): void;
  offClick(callback: () => void): void;
}

interface TelegramMainButton extends TelegramButton {
  text: string;
  isActive: boolean;
  setText(text: string): void;
  enable(): void;
  disable(): void;
  showProgress(leaveActive?: boolean): void;
  hideProgress(): void;
}

/** The subset of `window.Telegram.WebApp` (telegram-web-app.js) this app uses. */
export interface TelegramWebApp {
  initData: string;
  platform: string;
  colorScheme: "light" | "dark";
  ready(): void;
  expand(): void;
  openLink(url: string, options?: { try_instant_view?: boolean }): void;
  showConfirm(message: string, callback?: (confirmed: boolean) => void): void;
  BackButton: TelegramButton;
  MainButton: TelegramMainButton;
}

declare global {
  interface Window {
    Telegram?: { WebApp: TelegramWebApp };
  }
}

export function telegramWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

export function openExternalLink(url: string): void {
  const webApp = telegramWebApp();
  if (webApp) {
    webApp.openLink(url);
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/** Shows Telegram's native back button on every screen except home and maps it to history back. */
export function useTelegramBackButton(): void {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const isHome = pathname === "/";

  useEffect(() => {
    const button = telegramWebApp()?.BackButton;
    if (!button) {
      return;
    }
    if (isHome) {
      button.hide();
      return;
    }
    const goBack = () => {
      void navigate(-1);
    };
    button.onClick(goBack);
    button.show();
    return () => {
      button.offClick(goBack);
    };
  }, [isHome, navigate]);
}

export interface MainButtonOptions {
  text: string;
  onClick: () => void;
  enabled?: boolean;
  busy?: boolean;
}

/** Binds Telegram's bottom MainButton to a screen's primary action while the screen is mounted. */
export function useTelegramMainButton({ text, onClick, enabled = true, busy = false }: MainButtonOptions): void {
  useEffect(() => {
    const button = telegramWebApp()?.MainButton;
    if (!button) {
      return;
    }
    button.setText(text);
    button.onClick(onClick);
    button.show();
    return () => {
      button.offClick(onClick);
      button.hide();
    };
  }, [text, onClick]);

  useEffect(() => {
    const button = telegramWebApp()?.MainButton;
    if (!button) {
      return;
    }
    if (enabled && !busy) {
      button.enable();
    } else {
      button.disable();
    }
    if (busy) {
      button.showProgress();
    } else {
      button.hideProgress();
    }
  }, [enabled, busy]);
}

export function confirmAction(message: string): Promise<boolean> {
  const webApp = telegramWebApp();
  if (!webApp) {
    return Promise.resolve(window.confirm(message));
  }
  return new Promise((resolve) => {
    webApp.showConfirm(message, (confirmed) => {
      resolve(confirmed);
    });
  });
}
