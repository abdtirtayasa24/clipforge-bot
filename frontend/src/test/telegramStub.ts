import { vi } from "vitest";
import type { TelegramWebApp } from "../telegram/webApp";

type ButtonStub = TelegramWebApp["BackButton"] & TelegramWebApp["MainButton"];

function buttonStub(): ButtonStub {
  return {
    text: "",
    isVisible: false,
    isActive: true,
    show: vi.fn(),
    hide: vi.fn(),
    onClick: vi.fn(),
    offClick: vi.fn(),
    setText: vi.fn(),
    enable: vi.fn(),
    disable: vi.fn(),
    showProgress: vi.fn(),
    hideProgress: vi.fn(),
  };
}

export function installTelegram(overrides: Partial<TelegramWebApp> = {}): TelegramWebApp {
  const webApp: TelegramWebApp = {
    initData: "query_id=1&hash=abc",
    platform: "android",
    colorScheme: "light",
    ready: vi.fn(),
    expand: vi.fn(),
    openLink: vi.fn(),
    showConfirm: vi.fn((_message: string, callback?: (ok: boolean) => void) => callback?.(true)),
    BackButton: buttonStub(),
    MainButton: buttonStub(),
    ...overrides,
  };
  window.Telegram = { WebApp: webApp };
  return webApp;
}

export function removeTelegram(): void {
  delete window.Telegram;
}
