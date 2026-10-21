import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { useDeactivateProduct, useProducts } from "../api/affiliate";
import { ApiError } from "../api/client";
import { usePatchSettings, useSettings } from "../api/settings";
import type { AdminSettings } from "../api/types";
import { ErrorMessage, Loading } from "../components/QueryState";

function AffiliateSettingsForm({ settings }: { settings: AdminSettings }) {
  const patch = usePatchSettings();
  const toggle = usePatchSettings();
  const [threshold, setThreshold] = useState(String(settings.affiliate_min_relevance));
  const [disclosure, setDisclosure] = useState(settings.affiliate_disclosure_text);
  const errors = patch.error instanceof ApiError ? patch.error.fieldErrors() : {};

  function save(event: FormEvent) {
    event.preventDefault();
    patch.mutate({ affiliate_min_relevance: Number(threshold), affiliate_disclosure_text: disclosure.trim() });
  }

  return (
    <form className="card" aria-label="Affiliate settings" onSubmit={save}>
      <h2>Settings</h2>
      <label className="checkbox">
        <input
          type="checkbox"
          checked={settings.affiliate_links_enabled}
          disabled={toggle.isPending}
          onChange={(event) => toggle.mutate({ affiliate_links_enabled: event.target.checked })}
        />
        Affiliate links
      </label>
      <p className="hint">
        Off: no matching runs and caption exports contain no affiliate links. Approved records are kept for audit.
      </p>
      {toggle.isError && <ErrorMessage error={toggle.error} />}
      <label>
        <span>Minimum relevance (0–1)</span>
        <input
          type="number"
          min={0}
          max={1}
          step={0.01}
          required
          value={threshold}
          onChange={(event) => setThreshold(event.target.value)}
        />
      </label>
      {errors.affiliate_min_relevance && <p className="error">{errors.affiliate_min_relevance}</p>}
      <label>
        <span>Disclosure text</span>
        <textarea
          rows={2}
          required
          maxLength={500}
          value={disclosure}
          onChange={(event) => setDisclosure(event.target.value)}
        />
      </label>
      {errors.affiliate_disclosure_text && <p className="error">{errors.affiliate_disclosure_text}</p>}
      {patch.isError && Object.keys(errors).length === 0 && <ErrorMessage error={patch.error} />}
      {patch.isSuccess && <p role="status">Saved. Applies to new matches.</p>}
      <button type="submit" disabled={patch.isPending}>
        Save affiliate settings
      </button>
    </form>
  );
}

function Catalog() {
  const products = useProducts();
  const deactivate = useDeactivateProduct();

  return (
    <section className="card">
      <div className="row">
        <h2>Product catalog</h2>
        <Link to="/affiliate/products/new">Add product</Link>
      </div>
      {products.isPending && <Loading />}
      {products.isError && <ErrorMessage error={products.error} />}
      {products.isSuccess &&
        (products.data.items.length === 0 ? (
          <p className="hint">No products yet.</p>
        ) : (
          <ul className="list" aria-label="Products">
            {products.data.items.map((product) => (
              <li key={product.id}>
                <Link to={`/affiliate/products/${product.id}`}>
                  <strong>{product.name}</strong> <span className="hint">{product.merchant}</span>
                </Link>
                <div className="row">
                  <span className="badge">{product.destination_domain}</span>
                  {!product.active && <span className="badge">inactive</span>}
                  {product.active && (
                    <button
                      type="button"
                      className="danger"
                      aria-label={`Deactivate ${product.name}`}
                      disabled={deactivate.isPending}
                      onClick={() => deactivate.mutate(product.id)}
                    >
                      Deactivate
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        ))}
      {deactivate.isError && <ErrorMessage error={deactivate.error} />}
    </section>
  );
}

export default function Affiliate() {
  const settings = useSettings();

  return (
    <>
      <h1>Affiliate links</h1>
      {settings.isPending && <Loading />}
      {settings.isError && <ErrorMessage error={settings.error} />}
      {settings.isSuccess && <AffiliateSettingsForm settings={settings.data} />}
      <Catalog />
    </>
  );
}
