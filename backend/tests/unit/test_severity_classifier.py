import pytest

from backend.app.application.services.severity_classifier import SeverityClassifier


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        ("Production Down", "P1"),
        ("Multiple Services Unavailable", "P1"),
        (
            "ERROR Service unavailable\nERROR Request processing failed",
            "P2",
        ),
        ("ERROR Database connection timeout", "P2"),
        ("ERROR CrashLoopBackOff", "P2"),
        ("WARN High latency detected", "P3"),
        ("Partial degradation observed", "P3"),
        ("Memory leak detected", "P3"),
        ("WARN Cache refresh took longer than expected", "P4"),
        ("Application started normally", "unknown"),
        ("Production is not down; partial degradation observed", "P3"),
    ],
)
def test_classifies_incident_severity(evidence: str, expected: str) -> None:
    assert SeverityClassifier().classify([evidence]) == expected


def test_p1_takes_precedence_over_lower_severity_signals() -> None:
    evidence = "Service unavailable; production down; high latency."

    assert SeverityClassifier().classify([evidence]) == "P1"
