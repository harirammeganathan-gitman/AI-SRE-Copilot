from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DashboardSummary:
    total_documents: int
    total_chunks: int
    total_rca_reports: int
