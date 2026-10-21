import { useQuery } from "@tanstack/react-query";
import { ApiError, api } from "./client";
import type { UploadOut } from "./types";

export const uploadKeys = { active: ["uploads", "active"] as const };

/** The one upload the server is receiving (ADR-032), or null. */
export function fetchActiveUpload(): Promise<UploadOut | null> {
  return api.get<UploadOut | null>("/uploads/active");
}

/** Cancelling an upload that already finished or no longer exists is not an error. */
export async function cancelUpload(uploadId: string): Promise<void> {
  try {
    await api.del(`/uploads/${uploadId}`);
  } catch (error) {
    if (!(error instanceof ApiError && (error.status === 404 || error.status === 409))) {
      throw error;
    }
  }
}

export function useActiveUpload() {
  return useQuery({ queryKey: uploadKeys.active, queryFn: fetchActiveUpload });
}
