-- Affiliate matching inputs (ADR-020): the analysis topic, keywords and transcript language per
-- candidate, plus cached product embeddings keyed by the embedded text.
ALTER TABLE clip_candidates
    ADD COLUMN topic TEXT,
    ADD COLUMN keywords TEXT[] NOT NULL DEFAULT '{}',
    ADD COLUMN language TEXT;

CREATE TABLE affiliate_product_embeddings (
    product_id UUID PRIMARY KEY REFERENCES affiliate_products (id) ON DELETE CASCADE,
    model TEXT NOT NULL,
    content_sha256 CHAR(64) NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    embedding REAL[] NOT NULL CHECK (cardinality(embedding) > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TRIGGER affiliate_product_embeddings_updated_at BEFORE UPDATE
    ON affiliate_product_embeddings FOR EACH ROW EXECUTE FUNCTION set_updated_at();

GRANT SELECT, INSERT, UPDATE, DELETE ON affiliate_product_embeddings TO clipforge_app;
ALTER TABLE affiliate_product_embeddings ENABLE ROW LEVEL SECURITY;
CREATE POLICY clipforge_app_all ON affiliate_product_embeddings TO clipforge_app
    USING (true) WITH CHECK (true);
