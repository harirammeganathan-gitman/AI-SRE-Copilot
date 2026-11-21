from backend.app.interfaces.api.schemas.common import ApiSchema


class DashboardSummaryResponse(ApiSchema):
    total_documents: int
    total_chunks: int
    total_rca_reports: int
