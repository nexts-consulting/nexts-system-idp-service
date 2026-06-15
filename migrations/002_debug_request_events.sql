CREATE TABLE debug_request_events (
    id             BIGSERIAL PRIMARY KEY,
    trace_id       UUID NOT NULL,
    job_id         UUID REFERENCES jobs(id) ON DELETE CASCADE,
    service        TEXT NOT NULL,
    step           TEXT NOT NULL,
    direction      TEXT NOT NULL,
    started_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    duration_ms    INT,
    status         TEXT,
    request        JSONB,
    response       JSONB,
    error          TEXT
);

CREATE INDEX idx_debug_events_job ON debug_request_events (job_id, started_at);
CREATE INDEX idx_debug_events_trace ON debug_request_events (trace_id, started_at);
