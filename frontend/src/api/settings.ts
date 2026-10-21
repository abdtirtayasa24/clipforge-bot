import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AdminSettings, SettingsPatch } from "./types";

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
    },
  });
}
