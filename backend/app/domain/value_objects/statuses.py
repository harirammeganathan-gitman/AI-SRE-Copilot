from enum import StrEnum


class DocumentType(StrEnum):
    HISTORICAL_INCIDENT = "historical_incident"
    RUNBOOK = "runbook"
    RCA_REPORT = "rca_report"
    DEPLOYMENT_GUIDE = "deployment_guide"
    POSTMORTEM = "postmortem"
    LOG = "log"


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"
    ARCHIVED = "archived"


class IncidentSeverity(StrEnum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"
    CLOSED = "closed"


class RcaReportStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    APPROVED = "approved"
    REJECTED = "rejected"
