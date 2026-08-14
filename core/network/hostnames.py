"""Pure hostname helpers used by the safe outbound HTTP transport."""

from __future__ import annotations


def normalize_domain(value: str) -> str:
    """Return a canonical IDNA hostname suitable for policy comparison."""

    if not isinstance(value, str):
        raise TypeError("domain must be a string")
    candidate = value.strip().rstrip(".")
    if not candidate:
        raise ValueError("domain must not be empty")
    if any(character.isspace() or ord(character) < 32 for character in candidate):
        raise ValueError("domain must not contain whitespace or control characters")
    if any(character in candidate for character in "/\\@?#"):
        raise ValueError("domain must be a hostname, not a URL")
    try:
        ascii_domain = candidate.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ValueError("domain is not a valid IDNA hostname") from exc
    if len(ascii_domain) > 253:
        raise ValueError("domain is too long")
    labels = ascii_domain.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or not all(character.isalnum() or character == "-" for character in label)
        for label in labels
    ):
        raise ValueError("domain is not a valid hostname")
    return ascii_domain


def _domain_matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith(f".{domain}")


def is_domain_allowed(
    hostname: str,
    *,
    allowed_domains: tuple[str, ...] = (),
    blocked_domains: tuple[str, ...] = (),
) -> bool:
    """Apply exact-or-subdomain policy; deny rules take precedence."""

    try:
        host = normalize_domain(hostname)
        allowed = tuple(normalize_domain(value) for value in allowed_domains)
        blocked = tuple(normalize_domain(value) for value in blocked_domains)
    except (TypeError, ValueError):
        return False
    if any(_domain_matches(host, domain) for domain in blocked):
        return False
    return not allowed or any(_domain_matches(host, domain) for domain in allowed)


__all__ = ["is_domain_allowed", "normalize_domain"]
