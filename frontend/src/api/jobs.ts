import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { DashboardOut, JobDetail, JobOut, JobStatus, Page } from "./types";

const POLL_MS = 5_000;
const ACTIVE_STATUSES: readonly JobStatus[] = ["queued", "running", "retry_scheduled"];

export function isActiveJob(job: JobOut): boolean {
  return ACTIVE_STATUSES.includes(job.status);
}

export const jobKeys = {
  all: ["jobs"] as const,
  list: (status: JobStatus | null, offset: number) => ["jobs", "list", status, offset] as const,
  detail: (id: string) => ["jobs", "detail", id] as const,
};

export function useDashboard() {
  return useQuery({ queryKey: ["dashboard"], queryFn: () => api.get<DashboardOut>("/dashboard") });
}

export function useJobs(status: JobStatus | null, offset = 0) {
  return useQuery({
    queryKey: jobKeys.list(status, offset),
    queryFn: () => api.get<Page<JobOut>>("/jobs", { status, offset, limit: 20 }),
    refetchInterval: (query) => (query.state.data?.items.some(isActiveJob) ? POLL_MS : false),
  });
}

export function useJob(id: string) {
  return useQuery({
    queryKey: jobKeys.detail(id),
    queryFn: () => api.get<JobDetail>(`/jobs/${id}`),
    refetchInterval: (query) => (query.state.data && isActiveJob(query.state.data) ? POLL_MS : false),
  });
}

/** Retry or cancel; the detail is refetched afterwards so the screen shows the server's state. */
export function useJobAction(id: string, action: "retry" | "cancel") {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<JobOut>(`/jobs/${id}/${action}`),
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: jobKeys.all });
      void queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
