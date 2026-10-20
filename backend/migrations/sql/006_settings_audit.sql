-- Settings audit trail (ADR-026): one row per changed setting or provider config.
CREATE TABLE app_setting_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    setting_key TEXT NOT NULL CHECK (length(setting_key) BETWEEN 1 AND 200),
    old_value JSONB,
    new_value JSONB,
    admin_user_id UUID NOT NULL REFERENCES admin_users (id) ON DELETE RESTRICT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX app_setting_events_key_idx ON app_setting_events (setting_key, id);

CREATE FUNCTION reject_audit_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END
$$;
REVOKE ALL ON FUNCTION reject_audit_change() FROM PUBLIC;

CREATE TRIGGER app_setting_events_append_only BEFORE UPDATE OR DELETE ON app_setting_events
    FOR EACH ROW EXECUTE FUNCTION reject_audit_change();
CREATE TRIGGER app_setting_events_no_truncate BEFORE TRUNCATE ON app_setting_events
    FOR EACH STATEMENT EXECUTE FUNCTION reject_audit_change();

GRANT SELECT, INSERT ON app_setting_events TO clipforge_app;
ALTER TABLE app_setting_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY clipforge_app_all ON app_setting_events TO clipforge_app
    USING (true) WITH CHECK (true);
