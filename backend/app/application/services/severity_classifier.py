import re
from collections.abc import Iterable
from typing import Literal

Severity = Literal["P1", "P2", "P3", "P4", "unknown"]

_P1_PATTERNS = (
    re.compile(r"\bproduction\s+(?:is\s+)?down\b", re.IGNORECASE),
    re.compile(r"\b(?:critical|major)\s+(?:service\s+)?(?:outage|out)\b", re.IGNORECASE),
    re.compile(r"\bmultiple\s+services?\s+(?:are\s+)?unavailable\b", re.IGNORECASE),
    re.compile(r"\ball\s+services?\s+(?:are\s+)?unavailable\b", re.IGNORECASE),
    re.compile(r"\bcomplete\s+(?:service\s+)?outage\b", re.IGNORECASE),
)
_P2_PATTERNS = (
    re.compile(r"\bservice\s+(?:is\s+)?unavailable\b", re.IGNORECASE),
    re.compile(r"\brequests?(?:\s+processing)?\s+(?:failed|failure|failing)\b", re.IGNORECASE),
    re.compile(r"\brequest\s+failure\b", re.IGNORECASE),
    re.compile(r"\bdatabase\s+connection\s+(?:timed?\s*out|timeout)\b", re.IGNORECASE),
    re.compile(r"\bconnection\s+pool\s+(?:is\s+)?exhausted\b", re.IGNORECASE),
    re.compile(r"\bcrashloopbackoff\b", re.IGNORECASE),
)
_P3_PATTERNS = (
    re.compile(r"\bhigh\s+latency\b", re.IGNORECASE),
    re.compile(r"\bnetwork\s+latency\b", re.IGNORECASE),
    re.compile(r"\bpartial(?:ly)?\s+degrad(?:ation|ed)\b", re.IGNORECASE),
    re.compile(r"\bmemory\s+leak\b", re.IGNORECASE),
)
_WARNING_PATTERN = re.compile(r"\bwarn(?:ing)?s?\b", re.IGNORECASE)
_NON_WARNING_FAILURE_PATTERN = re.compile(
    r"\b(?:error|fatal|exception|failed|failure|timeout|crash|outage|unavailable|down)\b",
    re.IGNORECASE,
)
_NEGATION_SUFFIX = re.compile(
    r"\b(?:no|not|never|without|isn't|wasn't|aren't|weren't|didn't|doesn't|don't)"
    r"\s+(?:(?:the|any|a|an|production|critical|service|services|request|requests)\s+){0,3}$",
    re.IGNORECASE,
)


class SeverityClassifier:
    def classify(self, evidence: Iterable[str]) -> Severity:
        context = "\n".join(value for value in evidence if value)
        if not context:
            return "unknown"

        rules: tuple[tuple[Severity, tuple[re.Pattern[str], ...]], ...] = (
            ("P1", _P1_PATTERNS),
            ("P2", _P2_PATTERNS),
            ("P3", _P3_PATTERNS),
        )
        for severity, patterns in rules:
            if any(_has_asserted_match(pattern, context) for pattern in patterns):
                return severity

        if (
            _WARNING_PATTERN.search(context)
            and not _NON_WARNING_FAILURE_PATTERN.search(context)
        ):
            return "P4"
        return "unknown"


def _has_asserted_match(pattern: re.Pattern[str], text: str) -> bool:
    for match in pattern.finditer(text):
        prefix = text[max(0, match.start() - 48) : match.start()]
        if not _NEGATION_SUFFIX.search(prefix):
            return True
    return False
