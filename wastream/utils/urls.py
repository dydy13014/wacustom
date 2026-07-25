from typing import Dict, List, Optional


# ===========================
# Domain Canonicalization
# ===========================
# Mirror domains rewritten to a single canonical domain. Host detection, dedup and
# debrid resolution all expect the canonical form. Add new mirrors here.
DOMAIN_ALIASES = {
    "trbt.cc": "turbobit.net",
    "turbobit.cc": "turbobit.net",
}


def canonicalize_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return url
    for alias, canonical in DOMAIN_ALIASES.items():
        if alias in url:
            url = url.replace(alias, canonical)
    return url


def canonicalize_results(results: List[Dict]) -> List[Dict]:
    for result in results or []:
        link = result.get("link")
        if link:
            result["link"] = canonicalize_url(link)
    return results
