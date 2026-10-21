import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { clipKeys } from "./clips";
import { api } from "./client";
import type { AffiliateLinkList, Page, ProductCreate, ProductOut, ReviewDecision } from "./types";

/** The catalog is small (ADR-020); one page at the API's maximum covers it. */
const CATALOG_LIMIT = 200;

const productKeys = {
  all: ["affiliate-products"] as const,
  list: (active: boolean | null) => ["affiliate-products", active] as const,
};

const linkKeys = (clipId: string) => ["affiliate-links", clipId] as const;

export function useProducts(active: boolean | null = null) {
  return useQuery({
    queryKey: productKeys.list(active),
    queryFn: () => api.get<Page<ProductOut>>("/affiliate/products", { active, limit: CATALOG_LIMIT }),
  });
}

export function useSaveProduct(productId: string | null) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (body: ProductCreate) =>
      productId === null
        ? api.post<ProductOut>("/affiliate/products", body)
        : api.patch<ProductOut>(`/affiliate/products/${productId}`, body),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: productKeys.all }),
  });
}

export function useDeactivateProduct() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (productId: string) => api.del(`/affiliate/products/${productId}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: productKeys.all }),
  });
}

export function useClipLinks(clipId: string) {
  return useQuery({
    queryKey: linkKeys(clipId),
    queryFn: () => api.get<AffiliateLinkList>(`/clips/${clipId}/affiliate-links`),
  });
}

export type LinkAction =
  | { kind: "review"; linkId: string; decision: ReviewDecision; primary?: boolean }
  | { kind: "manual"; productId: string }
  | { kind: "rematch" }
  | { kind: "remove"; linkId: string };

/** Resolves to the clip's updated link list, or undefined after a removal (204). */
function runLinkAction(clipId: string, action: LinkAction): Promise<AffiliateLinkList | undefined> {
  const base = `/clips/${clipId}/affiliate-links`;
  switch (action.kind) {
    case "review":
      return api.post<AffiliateLinkList>(`${base}/${action.linkId}/review`, {
        decision: action.decision,
        ...(action.primary ? { primary: true } : {}),
      });
    case "manual":
      return api.post<AffiliateLinkList>(base, { product_id: action.productId });
    case "rematch":
      return api.post<AffiliateLinkList>(`${base}/rematch`);
    case "remove":
      return api.del(`${base}/${action.linkId}`);
  }
}

/** Every link change can change the clip's caption export, so both are refetched. */
export function useLinkAction(clipId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (action: LinkAction) => runLinkAction(clipId, action),
    onSuccess: (links) => {
      if (links) {
        queryClient.setQueryData(linkKeys(clipId), links);
      } else {
        void queryClient.invalidateQueries({ queryKey: linkKeys(clipId) });
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: clipKeys.detail(clipId) });
    },
  });
}
