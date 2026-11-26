"""Downloading from job sites, politely and safely."""

import ipaddress
import json
import socket
import urllib.error
import urllib.request
from urllib.parse import urlparse

USER_AGENT = "job-hunter/0.1 (personal job search tool)"
MAX_BYTES = 15 * 1024 * 1024


class FetchError(Exception):
    pass


def fetch_bytes(url: str, timeout: int = 30, max_bytes: int = MAX_BYTES) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise FetchError(f"{url}: {e}") from e
    if len(data) > max_bytes:
        raise FetchError(f"{url}: too big")
    return data


def fetch_json(url: str, timeout: int = 30):
    try:
        return json.loads(fetch_bytes(url, timeout))
    except json.JSONDecodeError as e:
        raise FetchError(f"{url}: not JSON") from e


def check_public_url(url: str) -> str:
    """Refuse links that point inside this laptop or home network.

    Pasted links are untrusted. Without this check, a link like
    http://127.0.0.1:8000/... could make the website call itself.
    """
    parts = urlparse(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http or https links work.")
    try:
        infos = socket.getaddrinfo(parts.hostname, None)
    except socket.gaierror as e:
        raise FetchError("That website name doesn't exist.") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise FetchError("That link points to a private address.")
    return url.strip()
