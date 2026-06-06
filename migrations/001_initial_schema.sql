-- IDP initial schema
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

CREATE TYPE job_status AS ENUM (
    'PENDING', 'DOWNLOADING', 'PREPROCESSING', 'FRAUD_DETECTED',
    'READY_FOR_EXTRACTION', 'BATCHING', 'EXTRACTING', 'EXTRACTED',
    'EXTRACTION_FAILED', 'RULES_APPLIED', 'COMPLETED', 'FAILED'
);

CREATE TABLE prompt_profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name VARCHAR(255) NOT NULL,
    invoice_type VARCHAR(64) NOT NULL,
    version VARCHAR(32) NOT NULL DEFAULT '1.0.0',
    system_prompt TEXT NOT NULL,
    user_template TEXT NOT NULL,
    json_schema JSONB NOT NULL DEFAULT '{}',
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (name, version)
);

CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
    status job_status NOT NULL DEFAULT 'PENDING',
    image_urls JSONB NOT NULL,
    invoice_type VARCHAR(64),
    prompt_profile_id UUID REFERENCES prompt_profiles(id),
    callback_url TEXT,
    metadata JSONB NOT NULL DEFAULT '{}',
    idempotency_key VARCHAR(128),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX idx_jobs_idempotency ON jobs (tenant_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE INDEX idx_jobs_tenant_status ON jobs (tenant_id, status);
CREATE INDEX idx_jobs_created ON jobs (created_at DESC);

CREATE TABLE job_stages (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    stage VARCHAR(32) NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ended_at TIMESTAMPTZ,
    error_code VARCHAR(64),
    metadata JSONB NOT NULL DEFAULT '{}'
);

CREATE INDEX idx_job_stages_job ON job_stages (job_id);

CREATE TABLE job_artifacts (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    artifact_type VARCHAR(32) NOT NULL,
    gcs_uri TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_job_artifacts_job ON job_artifacts (job_id);

CREATE TABLE fraud_results (
    job_id UUID PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    tamper_ratio DOUBLE PRECISION NOT NULL,
    tamper_pixels INTEGER NOT NULL,
    total_pixels INTEGER NOT NULL,
    predicted_tampered BOOLEAN NOT NULL,
    mask_gcs_uri TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE extraction_results (
    job_id UUID PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    batch_id VARCHAR(64),
    raw_json JSONB,
    validated_json JSONB,
    model_version VARCHAR(128),
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE job_results (
    job_id UUID PRIMARY KEY REFERENCES jobs(id) ON DELETE CASCADE,
    rules_result JSONB NOT NULL DEFAULT '{}',
    final_payload JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE rules (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id VARCHAR(128) NOT NULL DEFAULT 'default',
    name VARCHAR(255) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    condition_json JSONB NOT NULL,
    action_json JSONB NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_rules_tenant_priority ON rules (tenant_id, priority);

CREATE TABLE outbox_callbacks (
    id BIGSERIAL PRIMARY KEY,
    job_id UUID NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    callback_url TEXT NOT NULL,
    payload JSONB NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 5,
    next_retry_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_outbox_pending ON outbox_callbacks (next_retry_at)
    WHERE delivered_at IS NULL;

CREATE OR REPLACE FUNCTION update_jobs_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER jobs_updated_at
    BEFORE UPDATE ON jobs
    FOR EACH ROW EXECUTE FUNCTION update_jobs_updated_at();

CREATE TRIGGER rules_updated_at
    BEFORE UPDATE ON rules
    FOR EACH ROW EXECUTE FUNCTION update_jobs_updated_at();
