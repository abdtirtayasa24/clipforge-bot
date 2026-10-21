import { render } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";
import { installTelegram, removeTelegram } from "../test/telegramStub";
import { openExternalLink, useTelegramBackButton, useTelegramMainButton } from "./webApp";

afterEach(() => {
  removeTelegram();
});

function BackButtonProbe() {
  useTelegramBackButton();
  return null;
}

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="*" element={<BackButtonProbe />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("useTelegramBackButton", () => {
  it("hides the back button on the home screen", () => {
    const webApp = installTelegram();
    renderAt("/");
    expect(webApp.BackButton.hide).toHaveBeenCalled();
    expect(webApp.BackButton.show).not.toHaveBeenCalled();
  });

  it("shows the back button on nested screens and unregisters on unmount", () => {
    const webApp = installTelegram();
    const view = renderAt("/jobs/1");
    expect(webApp.BackButton.show).toHaveBeenCalled();
    expect(webApp.BackButton.onClick).toHaveBeenCalledOnce();
    view.unmount();
    expect(webApp.BackButton.offClick).toHaveBeenCalledOnce();
  });
});

describe("openExternalLink", () => {
  it("uses Telegram openLink inside Telegram", () => {
    const webApp = installTelegram();
    openExternalLink("https://drive.google.com/file/d/1/view");
    expect(webApp.openLink).toHaveBeenCalledWith("https://drive.google.com/file/d/1/view");
  });

  it("falls back to window.open outside Telegram", () => {
    const open = vi.spyOn(window, "open").mockReturnValue(null);
    openExternalLink("https://example.com");
    expect(open).toHaveBeenCalledWith("https://example.com", "_blank", "noopener,noreferrer");
  });
});

function MainButtonProbe({ enabled, onClick }: { enabled: boolean; onClick: () => void }) {
  useTelegramMainButton({ text: "Save", onClick, enabled });
  return null;
}

describe("useTelegramMainButton", () => {
  it("shows the main button with the action and hides it on unmount", () => {
    const webApp = installTelegram();
    const onClick = vi.fn();
    const view = render(<MainButtonProbe enabled={false} onClick={onClick} />);

    expect(webApp.MainButton.setText).toHaveBeenCalledWith("Save");
    expect(webApp.MainButton.onClick).toHaveBeenCalledWith(onClick);
    expect(webApp.MainButton.show).toHaveBeenCalled();
    expect(webApp.MainButton.disable).toHaveBeenCalled();

    view.rerender(<MainButtonProbe enabled onClick={onClick} />);
    expect(webApp.MainButton.enable).toHaveBeenCalled();

    view.unmount();
    expect(webApp.MainButton.offClick).toHaveBeenCalledWith(onClick);
    expect(webApp.MainButton.hide).toHaveBeenCalled();
  });
});
