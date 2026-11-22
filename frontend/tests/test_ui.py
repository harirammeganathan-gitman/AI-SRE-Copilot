import pytest

from backend.app.core.config import Settings
from frontend import ui
from frontend.ui import _rca_request_timeout_seconds


class LegacySettings(Settings):
    @property
    def rca_request_timeout_seconds(self) -> float:
        raise AttributeError("computed timeout is unavailable")


def test_rca_timeout_uses_base_settings_fields() -> None:
    settings = LegacySettings(_env_file=None)  # type: ignore[call-arg]

    assert _rca_request_timeout_seconds(settings) == pytest.approx(690.75)


class _Column:
    def __enter__(self) -> "_Column":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_rca_report_renders_findings_once_and_shows_quality_score(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    headings: list[str] = []
    rendered_text: list[str] = []
    metrics: list[tuple[str, str]] = []

    class FakeStreamlit:
        @staticmethod
        def subheader(value: str) -> None:
            headings.append(value)

        @staticmethod
        def columns(count: int) -> list[_Column]:
            return [_Column() for _ in range(count)]

        @staticmethod
        def metric(label: str, value: str) -> None:
            metrics.append((label, value))

        @staticmethod
        def markdown(value: str) -> None:
            rendered_text.append(value)

        @staticmethod
        def caption(value: str) -> None:
            rendered_text.append(value)

        @staticmethod
        def info(value: str) -> None:
            rendered_text.append(value)

    monkeypatch.setattr(ui, "st", FakeStreamlit())
    ui.render_rca_report(
        {
            "root_cause": "Connection pool exhausted",
            "impact": "Service unavailable",
            "severity": "P2",
            "confidence_score": 0.82,
            "consistency_score": 92,
            "degraded": False,
            "evidence": [
                {
                    "category": "root_cause",
                    "claim": "Connection pool exhausted",
                    "source_ids": ["chunk-1"],
                },
                {
                    "category": "symptom",
                    "claim": "Database connection timeout",
                    "source_ids": ["chunk-1"],
                },
                {
                    "category": "impact",
                    "claim": "Service unavailable",
                    "source_ids": ["chunk-1"],
                },
                {
                    "category": "consequence",
                    "claim": "Request processing failed",
                    "source_ids": ["chunk-1"],
                },
            ],
            "sources": [
                {
                    "chunk_id": "chunk-1",
                    "source_file": "db_failure.log",
                    "start_line": 0,
                    "end_line": 3,
                }
            ],
            "recommendations": ["Review database utilization."],
            "preventive_actions": ["Monitor connection-pool saturation."],
            "limitations": [],
        }
    )

    all_rendered = "\n".join(rendered_text)
    assert headings == [
        "Assessment",
        "Findings",
        "Evidence",
        "Recommendations",
        "Preventive Actions",
        "Limitations",
    ]
    assert all_rendered.count("Connection pool exhausted") == 1
    assert all_rendered.count("Service unavailable") == 1
    assert all_rendered.count("Database connection timeout") == 1
    assert all_rendered.count("Request processing failed") == 1
    assert all_rendered.count("db_failure.log") == 1
    assert ("Consistency score", "92/100") in metrics
