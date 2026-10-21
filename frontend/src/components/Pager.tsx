import type { Page } from "../api/types";

export function Pager({ page, onOffsetChange }: { page: Page<unknown>; onOffsetChange: (offset: number) => void }) {
  if (page.total <= page.limit) {
    return null;
  }
  const end = Math.min(page.offset + page.items.length, page.total);
  return (
    <div className="row">
      <button
        type="button"
        className="secondary"
        disabled={page.offset === 0}
        onClick={() => onOffsetChange(Math.max(0, page.offset - page.limit))}
      >
        Previous
      </button>
      <span className="hint">
        {page.offset + 1}–{end} of {page.total}
      </span>
      <button
        type="button"
        className="secondary"
        disabled={end >= page.total}
        onClick={() => onOffsetChange(page.offset + page.limit)}
      >
        Next
      </button>
    </div>
  );
}
