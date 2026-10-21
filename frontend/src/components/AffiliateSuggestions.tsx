import { useState } from "react";
import { type LinkAction, useClipLinks, useLinkAction, useProducts } from "../api/affiliate";
import type { AffiliateLinkOut } from "../api/types";
import { ErrorMessage, Loading } from "./QueryState";
import { StatusPill } from "./StatusPill";

function LinkItem({
  link,
  enabled,
  busy,
  act,
}: {
  link: AffiliateLinkOut;
  enabled: boolean;
  busy: boolean;
  act: (action: LinkAction) => void;
}) {
  const name = link.product.name;
  return (
    <li>
      <div className="row">
        <strong>{name}</strong>
        <span className="hint">{link.product.merchant}</span>
        <StatusPill status={link.status} />
        {link.is_primary && <span className="badge info">primary</span>}
      </div>
      <div className="hint">relevance {link.relevance_score.toFixed(2)}</div>
      {link.match_reason && <p>{link.match_reason}</p>}
      <div className="row">
        {enabled && link.status !== "approved" && (
          <button
            type="button"
            className="small"
            aria-label={`Approve ${name}`}
            disabled={busy}
            onClick={() => act({ kind: "review", linkId: link.id, decision: "approved" })}
          >
            Approve
          </button>
        )}
        {enabled && link.status === "approved" && !link.is_primary && (
          <button
            type="button"
            className="secondary small"
            aria-label={`Make ${name} primary`}
            disabled={busy}
            onClick={() => act({ kind: "review", linkId: link.id, decision: "approved", primary: true })}
          >
            Make primary
          </button>
        )}
        {enabled && link.status !== "rejected" && (
          <button
            type="button"
            className="danger small"
            aria-label={`Reject ${name}`}
            disabled={busy}
            onClick={() => act({ kind: "review", linkId: link.id, decision: "rejected" })}
          >
            Reject
          </button>
        )}
        <button
          type="button"
          className="danger small"
          aria-label={`Remove ${name}`}
          disabled={busy}
          onClick={() => act({ kind: "remove", linkId: link.id })}
        >
          Remove
        </button>
      </div>
    </li>
  );
}

function ManualPick({ linked, busy, act }: { linked: Set<string>; busy: boolean; act: (action: LinkAction) => void }) {
  const products = useProducts(true);
  const [productId, setProductId] = useState("");
  const options = products.data?.items.filter((product) => !linked.has(product.id)) ?? [];

  return (
    <div className="row">
      <label>
        <span>Catalog product</span>
        <select value={productId} onChange={(event) => setProductId(event.target.value)}>
          <option value="">Choose…</option>
          {options.map((product) => (
            <option key={product.id} value={product.id}>
              {product.name} · {product.merchant}
            </option>
          ))}
        </select>
      </label>
      <button
        type="button"
        className="secondary"
        disabled={busy || productId === ""}
        onClick={() => {
          act({ kind: "manual", productId });
          setProductId("");
        }}
      >
        Add product
      </button>
      {products.isError && <ErrorMessage error={products.error} />}
    </div>
  );
}

export function AffiliateSuggestions({ clipId }: { clipId: string }) {
  const links = useClipLinks(clipId);
  const action = useLinkAction(clipId);

  if (links.isPending) {
    return <Loading />;
  }
  if (links.isError) {
    return <ErrorMessage error={links.error} />;
  }
  const { enabled, items } = links.data;
  const act = (next: LinkAction) => action.mutate(next);
  const busy = action.isPending;

  return (
    <section className="card" aria-label="Affiliate products">
      <h2>Affiliate products</h2>
      {!enabled && (
        <p className="hint">
          Affiliate links are off: nothing is matched or exported. Earlier records stay for audit and can be removed.
        </p>
      )}
      {items.length === 0 ? (
        enabled && <p className="hint">No suggestions. Matching never invents links.</p>
      ) : (
        <ul className="list">
          {items.map((link) => (
            <LinkItem key={link.id} link={link} enabled={enabled} busy={busy} act={act} />
          ))}
        </ul>
      )}
      {enabled && (
        <>
          <ManualPick linked={new Set(items.map((link) => link.product.id))} busy={busy} act={act} />
          <button type="button" className="secondary" disabled={busy} onClick={() => act({ kind: "rematch" })}>
            Rematch
          </button>
        </>
      )}
      {action.isError && <ErrorMessage error={action.error} />}
    </section>
  );
}
