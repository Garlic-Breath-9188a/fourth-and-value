"""
Which publication each article claim came from.

The claims carry short codes (A1, S14, F-FBG). Those are meaningless to anyone
who has not read the source docs, so they are resolved to the actual
publication here.
"""
from __future__ import annotations
import re

# A1-A10 are RotoWire's "DFS Football 101" series; A11 and A12 are one-off
# guides from other publications. S-codes are the Stokastic NFL DFS hub.
_SITE_BY_CODE = {
    **{f"A{n}": "RotoWire" for n in range(1, 11)},
    "A11": "Footballguys",
    "A12": "RotoGrinders",
}
# Longest prefixes first, so "F-Medium" is not swallowed by the generic "F-".
_PREFIX = [("F-FBG", "Footballguys"), ("F-Medium", "Medium"),
           ("F-WINDAILY", "Win Daily Sports"), ("F-", "Other"), ("S", "Stokastic")]

SITE_URL = {
    "RotoWire": "rotowire.com",
    "Footballguys": "footballguys.com",
    "RotoGrinders": "rotogrinders.com",
    "Stokastic": "stokastic.com",
    "Medium": "medium.com",
    "Win Daily Sports": "windailysports.com",
}


def sites(articles: str) -> str:
    """`"A2, A6, A11"` -> `"RotoWire, Footballguys"`, deduplicated and ordered."""
    found: list[str] = []
    text = articles or ""
    for token in re.split(r"[,;]", text):
        token = token.strip()
        if not token:
            continue
        # Prefixes are matched against the RAW token: extracting a code first
        # truncated "F-Medium" to "F-M", which then fell through to the generic
        # "F-" rule and reported the wrong publication.
        site = None
        for prefix, name in _PREFIX:
            if token.startswith(prefix) and not token[0:1].isdigit():
                site = name
                break
        if site is None:
            code = re.match(r"([A-Z]+\d*)", token)
            key = code.group(1) if code else token
            site = _SITE_BY_CODE.get(key)
        # An A-code beyond the known list is still RotoWire's series.
        if site is None and key.startswith("A"):
            site = "RotoWire"
        if site and site not in found:
            found.append(site)
    return ", ".join(found) if found else "—"
