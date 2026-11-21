class DomainError(Exception):
    """Base class for violations of domain invariants."""


class DomainValidationError(DomainError):
    """Raised when a domain value violates a business invariant."""
