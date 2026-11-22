import re
from collections.abc import Sequence
from dataclasses import dataclass

_UNCERTAIN_ROOT_CAUSE = re.compile(
    r"\b(?:unable to determine|cannot determine|unknown|undetermined|not established)\b",
    re.IGNORECASE,
)
_CONNECTION_POOL = re.compile(
    r"\b(?:connection|database|db)\s+pool\s+"
    r"(?:(?:is|was|has been)\s+)?(?:exhausted|saturated)\b|"
    r"\bpool\s+exhaustion\b",
    re.IGNORECASE,
)
_MEMORY_LEAK = re.compile(r"\bmemory leak\b", re.IGNORECASE)
_CRASH_LOOP = re.compile(r"\bcrashloopbackoff\b", re.IGNORECASE)
_NETWORK_LATENCY = re.compile(r"\b(?:network\s+)?latency\b", re.IGNORECASE)
_DATABASE_TIMEOUT = re.compile(
    r"\b(?:database|db)(?:\s+connection)?\s+(?:timed?\s*out|timeout)\b",
    re.IGNORECASE,
)
_SERVICE_UNAVAILABLE = re.compile(r"\bservice\s+unavailable\b", re.IGNORECASE)
_ACTION_VERB = re.compile(
    r"\b(?:add|alert|configure|check|identify|implement|inspect|increase|measure|"
    r"monitor|profile|reduce|review|set|test|trace|track|tune|validate|verify)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class RcaActions:
    recommendations: tuple[str, ...]
    preventive_actions: tuple[str, ...]


class RcaActionGenerator:
    def generate(self, root_cause: str, evidence_context: str) -> RcaActions:
        if not root_cause.strip() or _UNCERTAIN_ROOT_CAUSE.search(root_cause):
            return RcaActions((), ())

        cause = root_cause + "\n" + evidence_context
        if _CONNECTION_POOL.search(root_cause):
            return RcaActions(
                recommendations=(
                    "Increase the pool size after validating database capacity and peak demand.",
                    "Review database utilization and connection lifecycle management.",
                    "Inspect long-running transactions that may hold connections.",
                ),
                preventive_actions=(
                    "Monitor pool saturation, connection utilization, and wait time.",
                    "Configure alerts for pool exhaustion and acquisition timeouts.",
                    "Review pool capacity against peak connection demand.",
                ),
            )
        if _MEMORY_LEAK.search(cause):
            return RcaActions(
                recommendations=(
                    "Profile heap growth to identify the leaking allocation path.",
                    "Inspect recent changes and object lifecycles in the affected service.",
                ),
                preventive_actions=(
                    "Monitor memory growth over time and alert on sustained increases.",
                    "Add memory regression checks for long-running workloads.",
                ),
            )
        if _CRASH_LOOP.search(cause):
            return RcaActions(
                recommendations=(
                    "Inspect the container's previous logs and termination reason.",
                    "Check startup probes, resource limits, configuration, and dependencies.",
                ),
                preventive_actions=(
                    "Alert on restart counts and CrashLoopBackOff status.",
                    "Add deployment smoke tests and validate probe behavior before rollout.",
                ),
            )
        if _NETWORK_LATENCY.search(cause):
            return RcaActions(
                recommendations=(
                    "Trace latency across service hops and inspect saturation and packet loss.",
                    "Check recent DNS, routing, load-balancer, and network changes.",
                ),
                preventive_actions=(
                    "Monitor p95 and p99 latency by dependency.",
                    "Alert on sustained latency and request-error increases.",
                ),
            )
        if _DATABASE_TIMEOUT.search(cause):
            return RcaActions(
                recommendations=(
                    "Inspect database availability, lock waits, query duration, and pool waits.",
                    "Review database utilization and timeout settings against observed load.",
                ),
                preventive_actions=(
                    "Monitor database latency, active connections, pool wait, and timeout rate.",
                    "Alert before pool saturation or database timeout thresholds are exceeded.",
                ),
            )
        if _SERVICE_UNAVAILABLE.search(cause):
            return RcaActions(
                recommendations=(
                    "Identify the unavailable service and verify its dependencies and capacity.",
                    "Inspect traces and logs around the first unavailable event.",
                ),
                preventive_actions=(
                    "Monitor service availability and request error rates.",
                    "Alert on sustained unavailability and failed requests.",
                ),
            )
        return RcaActions(
            recommendations=(
                "Verify the root-cause mechanism against service telemetry and recent changes.",
                "Review the affected component's capacity and dependency health.",
            ),
            preventive_actions=(
                "Monitor the identified failure mode and alert before service impact.",
                "Review capacity, recovery, and rollback controls for the affected component.",
            ),
        )


def are_action_items_specific(items: Sequence[str]) -> bool:
    normalized = [item.strip().casefold() for item in items]
    return (
        bool(normalized)
        and len(set(normalized)) == len(normalized)
        and all(len(item) >= 12 and _ACTION_VERB.search(item) for item in normalized)
    )
