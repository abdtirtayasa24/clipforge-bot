import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { jobKeys } from "./jobs";
import type { Eligibility, JobOut, MediaOut, Page, RightsStatus, SourceStatus, VideoOut } from "./types";

export interface VideoFilters {
  eligibility: Eligibility | null;
  rights_status: RightsStatus | null;
  source_status: SourceStatus | null;
  source_id: string | null;
}

export function useVideos(filters: VideoFilters, offset: number) {
  return useQuery({
    queryKey: ["videos", "list", filters, offset],
    queryFn: () => api.get<Page<VideoOut>>("/videos", { ...filters, offset, limit: 20 }),
  });
}

export function useVideo(id: string) {
  return useQuery({ queryKey: ["videos", "detail", id], queryFn: () => api.get<VideoOut>(`/videos/${id}`) });
}

export function useVideoJobs(videoId: string) {
  return useQuery({
    queryKey: [...jobKeys.all, "video", videoId],
    queryFn: () => api.get<Page<JobOut>>("/jobs", { video_id: videoId, limit: 20 }),
  });
}

/** A new source or job changes video status, job lists and dashboard counts. */
export function useInvalidateVideoData() {
  const queryClient = useQueryClient();
  return () => {
    void queryClient.invalidateQueries({ queryKey: ["videos"] });
    void queryClient.invalidateQueries({ queryKey: jobKeys.all });
    void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
}

export function useRegisterMediaUrl(videoId: string) {
  const onSuccess = useInvalidateVideoData();
  return useMutation({
    mutationFn: (body: { url: string }) =>
      api.post<MediaOut>(`/videos/${videoId}/media`, body),
    onSuccess,
  });
}

/** One key per queue attempt: a retried request after a dropped response cannot queue twice. */
export function useQueueJob(videoId: string) {
  const onSuccess = useInvalidateVideoData();
  return useMutation({
    mutationFn: (idempotencyKey: string) =>
      api.post<JobOut>(`/videos/${videoId}/jobs`, undefined, { "Idempotency-Key": idempotencyKey }),
    onSuccess,
  });
}
