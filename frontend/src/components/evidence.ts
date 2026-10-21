import type { PermissionEvidence, PermissionType } from "../api/types";

export interface EvidenceDraft {
  type: PermissionType | "";
  statement: string;
  reference: string;
}

export const EMPTY_EVIDENCE: EvidenceDraft = { type: "", statement: "", reference: "" };

/** Mirrors the API's PermissionEvidence bounds so submit stays disabled until the server would accept it. */
export const STATEMENT_MIN = 10;

export function toEvidence(draft: EvidenceDraft): PermissionEvidence | null {
  const statement = draft.statement.trim();
  if (draft.type === "" || statement.length < STATEMENT_MIN) {
    return null;
  }
  return { type: draft.type, statement, reference: draft.reference.trim() || null };
}
