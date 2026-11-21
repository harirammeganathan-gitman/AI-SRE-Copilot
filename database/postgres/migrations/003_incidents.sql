CREATE TABLE incidents (
    incident_id UUID PRIMARY KEY,
    incident_number TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('P1', 'P2', 'P3', 'P4')),
    status TEXT NOT NULL CHECK (status IN ('open', 'investigating', 'resolved', 'closed')),
    detected_at TIMESTAMPTZ NOT NULL,
    service_name TEXT,
    environment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT incidents_number_format
        CHECK (incident_number ~ '^INC-[A-Z0-9]+(-[A-Z0-9]+)*$')
);

UPDATE documents
SET incident_id = (metadata ->> 'incident_id')::uuid
WHERE incident_id IS NULL
  AND metadata ->> 'incident_id' ~*
      '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';

WITH legacy_incidents AS (
    SELECT incident_id, MIN(event_at) AS detected_at
    FROM (
        SELECT incident_id, created_at AS event_at
        FROM documents
        WHERE incident_id IS NOT NULL
        UNION ALL
        SELECT incident_id, generated_at AS event_at
        FROM rca_reports
    ) AS incident_events
    GROUP BY incident_id
),
numbered_incidents AS (
    SELECT
        incident_id,
        detected_at,
        ROW_NUMBER() OVER (ORDER BY incident_id) AS sequence_number
    FROM legacy_incidents
)
INSERT INTO incidents (
    incident_id, incident_number, title, severity, status, detected_at
)
SELECT
    incident_id,
    'INC-LEGACY-' || LPAD(sequence_number::text, 5, '0'),
    'Imported legacy incident',
    'P3',
    'open',
    detected_at
FROM numbered_incidents;

ALTER TABLE documents
    ADD CONSTRAINT documents_incident_id_fkey
    FOREIGN KEY (incident_id) REFERENCES incidents (incident_id) ON DELETE RESTRICT;

ALTER TABLE rca_reports
    ADD CONSTRAINT rca_reports_incident_id_fkey
    FOREIGN KEY (incident_id) REFERENCES incidents (incident_id) ON DELETE RESTRICT;

ALTER TABLE documents DROP CONSTRAINT documents_content_hash_key;

CREATE UNIQUE INDEX documents_content_hash_incident_unique
    ON documents (content_hash, incident_id)
    WHERE incident_id IS NOT NULL;

CREATE UNIQUE INDEX documents_content_hash_unassigned_unique
    ON documents (content_hash)
    WHERE incident_id IS NULL;

CREATE INDEX documents_incident_created_at_idx
    ON documents (incident_id, created_at DESC, document_id DESC);
