CREATE TABLE IF NOT EXISTS model_pricing (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    provider text NOT NULL,
    model text NOT NULL,
    input_usd_per_1m_tokens numeric,
    output_usd_per_1m_tokens numeric,
    cached_input_usd_per_1m_tokens numeric,
    reasoning_usd_per_1m_tokens numeric,
    effective_from timestamptz NOT NULL DEFAULT now(),
    effective_to timestamptz,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (effective_to IS NULL OR effective_to > effective_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_model_pricing_provider_model_from
    ON model_pricing(provider, model, effective_from);

CREATE INDEX IF NOT EXISTS idx_model_pricing_lookup
    ON model_pricing(provider, model, effective_from DESC)
    WHERE effective_to IS NULL;

INSERT INTO schema_migrations (id) VALUES ('004_model_pricing') ON CONFLICT DO NOTHING;
