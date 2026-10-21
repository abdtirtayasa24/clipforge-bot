import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { SourceCreate, SourceOut, SourceUpdate } from "./types";

const SOURCES = ["sources"] as const;

export function useSources() {
  return useQuery({
    queryKey: SOURCES,
    queryFn: async () => (await api.get<{ items: SourceOut[] }>("/sources")).items,
  });
}

export function useCreateSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: SourceCreate) => api.post<SourceOut>("/sources", body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SOURCES }),
  });
}

export function useUpdateSource() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: SourceUpdate }) => api.patch<SourceOut>(`/sources/${id}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: SOURCES }),
  });
}

export function useRunDiscovery() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<Record<string, string>>("/discovery/run"),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: SOURCES });
      void queryClient.invalidateQueries({ queryKey: ["videos"] });
    },
  });
}
