CREATE TABLE documents (
    document_id UUID PRIMARY KEY,
    document_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'processing', 'indexed', 'failed', 'archived')
    ),
    title TEXT NOT NULL,
    source_uri TEXT,
    incident_id UUID,
    content_hash CHAR(64) NOT NULL UNIQUE,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT documents_content_hash_sha256 CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX documents_status_created_at_idx
    ON documents (status, created_at DESC, document_id DESC);

CREATE TABLE document_chunks (
    chunk_id UUID PRIMARY KEY,
    document_id UUID NOT NULL REFERENCES documents (document_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL CHECK (chunk_index >= 0),
    content_hash CHAR(64) NOT NULL,
    text TEXT NOT NULL,
    metadata JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    CONSTRAINT document_chunks_document_index_unique UNIQUE (document_id, chunk_index),
    CONSTRAINT document_chunks_content_hash_sha256 CHECK (content_hash ~ '^[0-9a-f]{64}$')
);

CREATE INDEX document_chunks_document_index_idx
    ON document_chunks (document_id, chunk_index);
