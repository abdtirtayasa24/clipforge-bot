import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AdminSettings, DriveStatus, ProviderOut, ProviderPatch, SettingsPatch } from "./types";

const SETTINGS = ["settings"] as const;

export function useSettings() {
  return useQuery({ queryKey: SETTINGS, queryFn: () => api.get<AdminSettings>("/settings") });
}

export function usePatchSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (patch: SettingsPatch) => api.patch<AdminSettings>("/settings", patch),
    onSuccess: (settings) => {
      queryClient.setQueryData(SETTINGS, settings);
      void queryClient.invalidateQueries({ queryKey: ["affiliate-links"] });
      void queryClient.invalidateQueries({ queryKey: ["clips"] });
      void queryClient.invalidateQueries({ queryKey: ["ai-providers"] });
    },
  });
}

export function useProviders() {
  return useQuery({
    queryKey: ["ai-providers"],
    queryFn: async () => (await api.get<{ items: ProviderOut[] }>("/ai/providers")).items,
  });
}

/** Provider edits go through PATCH /settings; the provider list is refetched to show them. */
export function usePatchProvider() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (provider: ProviderPatch) => api.patch<AdminSettings>("/settings", { providers: [provider] }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["ai-providers"] }),
  });
}

export function useDriveStatus() {
  return useQuery({
    queryKey: ["drive-status"],
    queryFn: () => api.get<DriveStatus>("/integrations/drive/status"),
  });
}

export function useDriveConnect() {
  return useMutation({
    mutationFn: async () => (await api.get<{ auth_url: string }>("/integrations/drive/connect")).auth_url,
  });
}
