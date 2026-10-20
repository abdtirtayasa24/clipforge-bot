-- Affiliate catalog and per-clip links, as in the spec.
CREATE TABLE affiliate_products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    merchant TEXT NOT NULL,
    affiliate_url TEXT NOT NULL,
    destination_domain TEXT NOT NULL,
    category TEXT,
    tags TEXT[] NOT NULL DEFAULT '{}',
    language_code TEXT,
    region_code VARCHAR(2),
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE clip_affiliate_links (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    clip_id UUID NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    product_id UUID NOT NULL REFERENCES affiliate_products(id),
    relevance_score NUMERIC(4,3) NOT NULL
        CHECK (relevance_score BETWEEN 0 AND 1),
    match_reason TEXT,
    status TEXT NOT NULL DEFAULT 'suggested'
        CHECK (status IN ('suggested','approved','rejected')),
    is_primary BOOLEAN NOT NULL DEFAULT false,
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (clip_id, product_id)
);

CREATE UNIQUE INDEX idx_one_primary_affiliate_per_clip
ON clip_affiliate_links (clip_id)
WHERE is_primary = true AND status = 'approved';

CREATE TABLE affiliate_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    clip_id UUID NOT NULL REFERENCES clips(id) ON DELETE CASCADE,
    affiliate_link_id UUID REFERENCES clip_affiliate_links(id),
    event_type TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Additions beyond the spec's DDL: triggers, grants, RLS and indexes.
CREATE TRIGGER affiliate_products_updated_at BEFORE UPDATE ON affiliate_products
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
-- Append-only, except that removing a link unlinks its events; the link and product ids
-- also live in `details`, so the history survives the removal.
CREATE FUNCTION protect_affiliate_event() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.affiliate_link_id IS NULL
        AND (NEW.id, NEW.clip_id, NEW.event_type, NEW.details, NEW.created_at)
            IS NOT DISTINCT FROM (OLD.id, OLD.clip_id, OLD.event_type, OLD.details, OLD.created_at)
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'affiliate_events is append-only';
END
$$;
REVOKE ALL ON FUNCTION protect_affiliate_event() FROM PUBLIC;

CREATE TRIGGER affiliate_events_append_only BEFORE UPDATE OR DELETE ON affiliate_events
    FOR EACH ROW EXECUTE FUNCTION protect_affiliate_event();
CREATE TRIGGER affiliate_events_no_truncate BEFORE TRUNCATE ON affiliate_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_change();

GRANT SELECT, INSERT, UPDATE ON affiliate_products TO clipforge_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON clip_affiliate_links TO clipforge_app;
GRANT SELECT, INSERT, UPDATE (affiliate_link_id) ON affiliate_events TO clipforge_app;

DO $$
DECLARE
    app_table TEXT;
BEGIN
    FOREACH app_table IN ARRAY ARRAY[
        'affiliate_products', 'clip_affiliate_links', 'affiliate_events'
    ] LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', app_table);
        EXECUTE format(
            'CREATE POLICY clipforge_app_all ON %I TO clipforge_app USING (true) WITH CHECK (true)',
            app_table);
    END LOOP;
END
$$;

CREATE INDEX affiliate_products_active_idx ON affiliate_products (active, created_at DESC);
CREATE INDEX clip_affiliate_links_product_id_idx ON clip_affiliate_links (product_id);
CREATE INDEX affiliate_events_clip_id_idx ON affiliate_events (clip_id, id);
CREATE INDEX affiliate_events_link_id_idx ON affiliate_events (affiliate_link_id);
