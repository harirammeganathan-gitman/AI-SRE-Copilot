from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class DashboardCounts:
    total_documents: int
    total_chunks: int
    total_rca_reports: int


class DashboardRepository(Protocol):
    async def get_counts(self) -> DashboardCounts: ...
