import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { ClipDetail, ClipOut, ClipReviewIn, Page, ReviewStatus } from "./types";

export const clipKeys = {
  all: ["clips"] as const,
  list: (status: ReviewStatus | null, offset: number) => ["clips", "list", status, offset] as const,
  detail: (id: string) => ["clips", "detail", id] as const,
};

export function useClips(status: ReviewStatus | null, offset: number) {
  return useQuery({
    queryKey: clipKeys.list(status, offset),
    queryFn: () => api.get<Page<ClipOut>>("/clips", { status, offset, limit: 20 }),
  });
}

export function useClip(id: string) {
  return useQuery({ queryKey: clipKeys.detail(id), queryFn: () => api.get<ClipDetail>(`/clips/${id}`) });
}

export function useReviewClip(id: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (review: ClipReviewIn) => api.post<ClipDetail>(`/clips/${id}/review`, review),
    onSuccess: (clip) => {
      queryClient.setQueryData(clipKeys.detail(id), clip);
      void queryClient.invalidateQueries({ queryKey: [...clipKeys.all, "list"] });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
