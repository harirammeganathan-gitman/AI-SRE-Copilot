import pytest

from backend.app.application.services.rca_action_generator import (
    RcaActionGenerator,
    are_action_items_specific,
)


@pytest.mark.parametrize(
    ("root_cause", "recommendation_fragment", "prevention_fragment"),
    [
        ("Connection pool exhausted", "pool size", "pool saturation"),
        ("Memory leak", "heap growth", "memory growth"),
        ("CrashLoopBackOff", "previous logs", "restart counts"),
        ("Network latency", "service hops", "p95"),
        ("Database connection timeout", "lock waits", "database latency"),
        ("Service unavailable", "unavailable service", "service availability"),
    ],
)
def test_generates_cause_specific_actions(
    root_cause: str,
    recommendation_fragment: str,
    prevention_fragment: str,
) -> None:
    actions = RcaActionGenerator().generate(root_cause, root_cause)

    assert any(recommendation_fragment in item.casefold() for item in actions.recommendations)
    assert any(prevention_fragment in item.casefold() for item in actions.preventive_actions)
    assert are_action_items_specific(actions.recommendations)
    assert are_action_items_specific(actions.preventive_actions)


def test_does_not_generate_actions_for_an_undetermined_root_cause() -> None:
    actions = RcaActionGenerator().generate(
        "Unable to determine root cause from the evidence.",
        "Database connection timeout",
    )

    assert actions.recommendations == ()
    assert actions.preventive_actions == ()
