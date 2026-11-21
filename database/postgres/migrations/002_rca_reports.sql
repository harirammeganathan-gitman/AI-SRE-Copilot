CREATE TABLE rca_reports (
    report_id UUID PRIMARY KEY,
    incident_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'failed', 'approved', 'rejected')
    ),
    version INTEGER NOT NULL CHECK (version > 0),
    generated_at TIMESTAMPTZ NOT NULL,
    root_cause TEXT,
    evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
    impact TEXT,
    resolution TEXT,
    preventive_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
    limitations JSONB NOT NULL DEFAULT '[]'::jsonb,
    severity TEXT NOT NULL CHECK (severity IN ('P1', 'P2', 'P3', 'P4', 'unknown')),
    recommendations JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence_claims JSONB NOT NULL DEFAULT '[]'::jsonb,
    CONSTRAINT rca_reports_incident_version_unique UNIQUE (incident_id, version)
);

CREATE INDEX rca_reports_generated_at_idx
    ON rca_reports (generated_at DESC, report_id DESC);

CREATE INDEX rca_reports_incident_generated_at_idx
    ON rca_reports (incident_id, generated_at DESC, report_id DESC);
