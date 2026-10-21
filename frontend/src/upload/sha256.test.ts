import { describe, expect, it, vi } from "vitest";
import { sha256File } from "./sha256";

const ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad";

describe("sha256File", () => {
  it("hashes the whole file", async () => {
    await expect(sha256File(new Blob(["abc"]))).resolves.toBe(ABC_SHA256);
  });

  it("gives the same digest when read in small slices and reports progress", async () => {
    const onProgress = vi.fn();

    const digest = await sha256File(new Blob(["a", "b", "c"]), onProgress, 1);

    expect(digest).toBe(ABC_SHA256);
    expect(onProgress.mock.calls).toEqual([
      [1, 3],
      [2, 3],
      [3, 3],
    ]);
  });
});
