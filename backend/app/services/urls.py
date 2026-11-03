"""Checking a URL that a credential is about to be sent to."""

from ipaddress import ip_address, ip_network
from urllib.parse import urlparse

from app.services.errors import ValidationError

_PRIVATE_NETWORKS = [
    ip_network("10.0.0.0/8"),
    ip_network("172.16.0.0/12"),
    ip_network("192.168.0.0/16"),
    ip_network("127.0.0.0/8"),
    # Tailscale / Headscale addresses: private in practice, and where a home server sits.
    ip_network("100.64.0.0/10"),
]


def validate_service_url(url: str, label: str) -> str:
    """https anywhere, http only on a private host: a key or password travels on this
    connection."""
    trimmed = url.strip()
    if not trimmed:
        raise ValidationError(f"{label} is required.")

    parsed = urlparse(trimmed)
    if parsed.scheme == "https" and parsed.hostname:
        return trimmed
    if parsed.scheme == "http" and is_private_host(parsed.hostname):
        return trimmed
    raise ValidationError(f"{label} must use https://, or http:// for a private network host.")


def is_private_host(hostname: str | None) -> bool:
    if hostname is None:
        return False
    if hostname == "localhost":
        return True
    try:
        addr = ip_address(hostname)
    except ValueError:
        return False
    return any(addr in network for network in _PRIVATE_NETWORKS)
