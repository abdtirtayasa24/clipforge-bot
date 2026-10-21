import { useId } from "react";
import type { PermissionType } from "../api/types";
import { type EvidenceDraft, STATEMENT_MIN } from "./evidence";

const TYPE_LABELS: Record<PermissionType, string> = {
  own_content: "My own content",
  creator_permission: "Permission from the creator",
  license: "Licensed content",
};

export function PermissionEvidenceFields({
  value,
  onChange,
  errors = {},
}: {
  value: EvidenceDraft;
  onChange: (value: EvidenceDraft) => void;
  errors?: Record<string, string>;
}) {
  const id = useId();
  return (
    <fieldset>
      <legend>Permission evidence (required)</legend>
      <label htmlFor={`${id}-type`}>
        <span>Basis</span>
      </label>
      <select
        id={`${id}-type`}
        required
        value={value.type}
        onChange={(event) => onChange({ ...value, type: event.target.value as PermissionType | "" })}
      >
        <option value="">Choose…</option>
        {Object.entries(TYPE_LABELS).map(([type, label]) => (
          <option key={type} value={type}>
            {label}
          </option>
        ))}
      </select>
      <label htmlFor={`${id}-statement`}>
        <span>Statement (at least {STATEMENT_MIN} characters)</span>
      </label>
      <textarea
        id={`${id}-statement`}
        required
        minLength={STATEMENT_MIN}
        maxLength={2000}
        rows={3}
        value={value.statement}
        onChange={(event) => onChange({ ...value, statement: event.target.value })}
      />
      {errors["permission.statement"] && <p className="error">{errors["permission.statement"]}</p>}
      <label htmlFor={`${id}-reference`}>
        <span>Reference (optional: licence ID, email, link)</span>
      </label>
      <input
        id={`${id}-reference`}
        maxLength={500}
        value={value.reference}
        onChange={(event) => onChange({ ...value, reference: event.target.value })}
      />
    </fieldset>
  );
}
