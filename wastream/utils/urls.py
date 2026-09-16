from ipaddress import ip_address
from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit


# ===========================
# Source URL Helpers
# ===========================
def get_url_origin(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None

        hostname = parsed.hostname.rstrip(".").lower()
        host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            host += f":{parsed.port}"
        return urlunsplit((parsed.scheme.lower(), host, "", "", ""))
    except ValueError:
        return None


def same_source_host(first_url: Optional[str], second_url: Optional[str]) -> bool:
    try:
        first = urlsplit(first_url or "")
        second = urlsplit(second_url or "")
        first_host = (first.hostname or "").rstrip(".").lower().removeprefix("www.")
        second_host = (second.hostname or "").rstrip(".").lower().removeprefix("www.")
        first_port = first.port
        second_port = second.port
        if first_port is None and second_port is not None:
            first_port = 443 if first.scheme == "https" else 80 if first.scheme == "http" else None
        if second_port is None and first_port is not None:
            second_port = 443 if second.scheme == "https" else 80 if second.scheme == "http" else None
        return bool(first_host and first_host == second_host and first_port == second_port)
    except ValueError:
        return False


def replace_url_origin(url: str, origin: str) -> str:
    try:
        parsed = urlsplit(url)
        parsed_origin = urlsplit(origin)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed_origin.scheme not in {"http", "https"}
            or not parsed_origin.hostname
        ):
            return url
        return urlunsplit(
            (
                parsed_origin.scheme,
                parsed_origin.netloc,
                parsed.path,
                parsed.query,
                parsed.fragment,
            )
        )
    except ValueError:
        return url


def get_www_url_variant(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None

        hostname = parsed.hostname.rstrip(".").lower()
        if hostname == "localhost" or "." not in hostname:
            return None
        try:
            ip_address(hostname)
            return None
        except ValueError:
            pass

        alternate = hostname.removeprefix("www.") if hostname.startswith("www.") else f"www.{hostname}"
        if not alternate:
            return None
        netloc = alternate
        if parsed.port is not None:
            netloc += f":{parsed.port}"
        return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return None


def url_matches_host(url: Optional[str], hosts: List[str]) -> bool:
    try:
        hostname = (
            urlsplit(url or "").hostname or ""
        ).rstrip(".").lower().removeprefix("www.")
    except ValueError:
        return False
    if not hostname:
        return False

    labels = hostname.split(".")
    for value in hosts:
        candidate = str(value or "").strip().lower()
        if "://" in candidate:
            try:
                candidate = urlsplit(candidate).hostname or ""
            except ValueError:
                continue
        candidate = candidate.rstrip(".").removeprefix("www.")
        if not candidate:
            continue
        if "." in candidate:
            if hostname == candidate or hostname.endswith(f".{candidate}"):
                return True
        elif hostname == candidate or (
            len(labels) >= 2 and labels[-2] == candidate
        ):
            return True
    return False


# ===========================
# Domain Canonicalization
# ===========================
# Mirror domains rewritten to a single canonical domain. Host detection, dedup and
# debrid resolution all expect the canonical form. Add new mirrors here.
DOMAIN_ALIASES = {
    "trbt.cc": "turbobit.net",
    "turbobit.cc": "turbobit.net",
    "trbbt.net": "turbobit.net",
    "turb.cc": "turbobit.net",
    "turb.pw": "turbobit.net",
    "rg.to": "rapidgator.net",
}


def canonicalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if not hostname:
            return url

        hostname = hostname.rstrip(".").lower()
        for alias, canonical in DOMAIN_ALIASES.items():
            if hostname != alias.lower().rstrip("."):
                continue

            userinfo = ""
            if "@" in parsed.netloc:
                userinfo = parsed.netloc.rsplit("@", 1)[0] + "@"

            host = f"[{canonical}]" if ":" in canonical else canonical
            if parsed.port is not None:
                host += f":{parsed.port}"

            return urlunsplit((parsed.scheme, userinfo + host, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return url

    return url


def canonicalize_results(results: List[Dict]) -> List[Dict]:
    for result in results or []:
        link = result.get("link")
        if link:
            result["link"] = canonicalize_url(link)
    return results
