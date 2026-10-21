import { describe, expect, it } from "vitest";
import { formatBytes, formatCost, formatDuration, formatTimestamp } from "./format";

describe("formatCost", () => {
  it("shows the currency and the estimated decimal unchanged", () => {
    expect(formatCost({ currency: "USD", estimated: "0.0015" })).toBe("USD 0.0015");
  });
});

describe("formatDuration", () => {
  it.each([
    [0, "0s"],
    [5_000, "5s"],
    [65_000, "1m 5s"],
    [3_725_000, "1h 2m"],
  ])("%i ms → %s", (ms, text) => {
    expect(formatDuration(ms)).toBe(text);
  });
});

describe("formatBytes", () => {
  it.each([
    [512, "512 B"],
    [8 * 1024 * 1024, "8.0 MiB"],
    [5 * 1024 ** 3, "5.0 GiB"],
  ])("%i → %s", (bytes, text) => {
    expect(formatBytes(bytes)).toBe(text);
  });
});

describe("formatTimestamp", () => {
  it.each([
    [0, "0:00"],
    [754_000, "12:34"],
    [3_723_900, "1:02:03"],
  ])("%i ms → %s", (ms, text) => {
    expect(formatTimestamp(ms)).toBe(text);
  });
});
