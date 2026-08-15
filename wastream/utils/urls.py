from typing import Dict, List, Optional
from urllib.parse import urlsplit, urlunsplit


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
