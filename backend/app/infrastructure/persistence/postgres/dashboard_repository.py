import asyncpg

from backend.app.domain.repositories.dashboard_repository import DashboardCounts


class PostgresDashboardRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_counts(self) -> DashboardCounts:
        async with self._pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT
                    (SELECT COUNT(*) FROM documents) AS total_documents,
                    (SELECT COUNT(*) FROM document_chunks) AS total_chunks,
                    (SELECT COUNT(*) FROM rca_reports) AS total_rca_reports
                """
            )
        if row is None:
            raise RuntimeError("Dashboard count query returned no result")
        return DashboardCounts(
            total_documents=row["total_documents"],
            total_chunks=row["total_chunks"],
            total_rca_reports=row["total_rca_reports"],
        )
