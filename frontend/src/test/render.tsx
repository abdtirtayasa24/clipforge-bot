import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter, Route, Routes } from "react-router";

interface RenderOptions {
  route?: string;
  path?: string;
}

/** Renders a page inside a fresh QueryClient (no retries) and a router positioned at `route`. */
export function renderPage(ui: ReactElement, { route = "/", path = "*" }: RenderOptions = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path={path} element={ui} />
          <Route path="*" element={<p>other page</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
