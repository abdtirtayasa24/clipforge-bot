import { type FormEvent, useState } from "react";
import { useNavigate, useParams } from "react-router";
import { useProducts, useSaveProduct } from "../api/affiliate";
import { ApiError } from "../api/client";
import type { ProductOut } from "../api/types";
import { PageHeader } from "../components/PageHeader";
import { ErrorMessage, Loading } from "../components/QueryState";

interface Draft {
  name: string;
  merchant: string;
  affiliate_url: string;
  destination_domain: string;
  category: string;
  tags: string;
  language_code: string;
  region_code: string;
  active: boolean;
}

function toDraft(product: ProductOut | null): Draft {
  return {
    name: product?.name ?? "",
    merchant: product?.merchant ?? "",
    affiliate_url: product?.affiliate_url ?? "",
    destination_domain: product?.destination_domain ?? "",
    category: product?.category ?? "",
    tags: product?.tags.join(", ") ?? "",
    language_code: product?.language_code ?? "",
    region_code: product?.region_code ?? "",
    active: product?.active ?? true,
  };
}

const optional = (value: string) => value.trim() || null;

const TEXT_FIELDS: { field: keyof Omit<Draft, "active">; label: string; required?: boolean; hint?: string }[] = [
  { field: "name", label: "Name", required: true },
  { field: "merchant", label: "Merchant", required: true },
  { field: "affiliate_url", label: "Affiliate URL", required: true, hint: "https only; tracking parameters are kept." },
  { field: "destination_domain", label: "Destination domain", required: true, hint: "The URL's host must match it." },
  { field: "category", label: "Category" },
  { field: "tags", label: "Tags (comma separated)" },
  { field: "language_code", label: "Language (e.g. id, en)" },
  { field: "region_code", label: "Region (e.g. ID)" },
];

function ProductForm({ product }: { product: ProductOut | null }) {
  const navigate = useNavigate();
  const save = useSaveProduct(product?.id ?? null);
  const [draft, setDraft] = useState(() => toDraft(product));
  const errors = save.error instanceof ApiError ? save.error.fieldErrors() : {};

  function submit(event: FormEvent) {
    event.preventDefault();
    save.mutate(
      {
        name: draft.name.trim(),
        merchant: draft.merchant.trim(),
        affiliate_url: draft.affiliate_url.trim(),
        destination_domain: draft.destination_domain.trim(),
        category: optional(draft.category),
        tags: draft.tags
          .split(",")
          .map((tag) => tag.trim())
          .filter(Boolean),
        language_code: optional(draft.language_code),
        region_code: optional(draft.region_code),
        active: draft.active,
      },
      { onSuccess: () => void navigate("/affiliate") },
    );
  }

  return (
    <form className="card" aria-label="Product" onSubmit={submit}>
      {TEXT_FIELDS.map(({ field, label, required, hint }) => (
        <div key={field}>
          <label>
            <span>{label}</span>
            <input
              required={required}
              value={draft[field]}
              onChange={(event) => setDraft({ ...draft, [field]: event.target.value })}
            />
          </label>
          {hint && <p className="hint">{hint}</p>}
          {(errors[field] ?? errors[`${field}.0`]) && <p className="error">{errors[field] ?? errors[`${field}.0`]}</p>}
        </div>
      ))}
      <label className="switch-row">
        Active
        <input
          type="checkbox"
          className="switch"
          checked={draft.active}
          onChange={(event) => setDraft({ ...draft, active: event.target.checked })}
        />
      </label>
      {save.isError && Object.keys(errors).length === 0 && <ErrorMessage error={save.error} />}
      {save.isError && errors[""] && <p className="error">{errors[""]}</p>}
      <button type="submit" className="block" disabled={save.isPending}>
        Save product
      </button>
    </form>
  );
}

function EditProduct({ productId }: { productId: string }) {
  const products = useProducts();

  if (products.isPending) {
    return <Loading />;
  }
  if (products.isError) {
    return <ErrorMessage error={products.error} />;
  }
  const product = products.data.items.find((item) => item.id === productId);
  if (!product) {
    return <ErrorMessage error={new Error("affiliate product not found")} />;
  }
  return <ProductForm product={product} />;
}

export default function AffiliateProductForm() {
  const { productId } = useParams();
  return (
    <>
      <PageHeader
        title={productId === undefined ? "New product" : "Edit product"}
        subtitle="Only https links on the product's own domain"
      />
      {productId === undefined ? <ProductForm product={null} /> : <EditProduct productId={productId} />}
    </>
  );
}
