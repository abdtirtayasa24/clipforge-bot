import type { CostOut } from "./api/types";

/** Costs stay decimal strings from the API so the shown amount is exactly the stored estimate. */
export function formatCost(cost: CostOut): string {
  return `${cost.currency} ${cost.estimated}`;
}

export function formatDuration(ms: number): string {
  const totalSeconds = Math.round(ms / 1000);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}h ${minutes}m`;
  }
  if (minutes > 0) {
    return `${minutes}m ${seconds}s`;
  }
  return `${seconds}s`;
}

const BYTE_UNITS = ["KiB", "MiB", "GiB", "TiB"];

export function formatBytes(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  let value = bytes;
  let unit = "B";
  for (const next of BYTE_UNITS) {
    if (value < 1024) {
      break;
    }
    value /= 1024;
    unit = next;
  }
  return `${value.toFixed(1)} ${unit}`;
}

export function formatDateTime(iso: string | null): string {
  if (iso === null) {
    return "—";
  }
  return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}
