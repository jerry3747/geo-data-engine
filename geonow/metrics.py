from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit


RECOMMEND = re.compile(r"推荐|建议(?:选择|使用)?|值得(?:购买|选择|考虑)|优先考虑|可以考虑|首选")


def _contains(text: str, term: str) -> bool:
    return term.casefold() in (text or "").casefold()


def _url_key(url: str) -> str:
    try:
        p = urlsplit(url)
        return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/"), p.query, ""))
    except Exception:
        return url.rstrip("/")


def _citation_hit(citations: list[dict[str, Any]], partner_urls: set[str]) -> bool:
    return any(_url_key(str(c.get("url", ""))) in partner_urls for c in citations)


def summarize(rows: Iterable[Any], brand_terms: list[str], competitors: list[str], partner_urls: list[str]) -> dict[str, Any]:
    rows = list(rows)
    groups: dict[tuple[str, str, str], list[Any]] = defaultdict(list)
    for row in rows:
        groups[(row["source"], row["provider"], row["batch_id"])].append(row)
    result = {"total_samples": len(rows), "failed_samples": sum(not bool(r["valid"]) for r in rows), "groups": []}
    partner_set = {_url_key(x) for x in partner_urls}
    for (source, provider, batch), items in groups.items():
        valid = [r for r in items if bool(r["valid"]) and (r["answer_text"] or r["raw_answer"])]
        den = len(valid)
        brand_mentions = 0
        recommendations = 0
        partner_citations = 0
        competitor_stats = {}
        for row in valid:
            text = row["answer_text"] or row["raw_answer"] or ""
            brand_hit = any(_contains(text, t) for t in brand_terms)
            brand_mentions += brand_hit
            recommendations += bool(brand_hit and RECOMMEND.search(text))
            try:
                citations = json.loads(row["citations_json"] or "[]")
            except (TypeError, json.JSONDecodeError):
                citations = []
            partner_citations += _citation_hit(citations, partner_set)
            for comp in competitors:
                stat = competitor_stats.setdefault(comp, {"mentions": 0, "recommendations": 0})
                hit = _contains(text, comp)
                stat["mentions"] += hit
                stat["recommendations"] += bool(hit and RECOMMEND.search(text))
        def rate(num: int) -> float | None:
            return round(num / den, 4) if den else None
        result["groups"].append({
            "source": source, "provider": provider, "batch_id": batch,
            "sample_count": len(items), "valid_sample_count": den,
            "failed_sample_count": len(items) - den,
            "brand_mention_rate": rate(brand_mentions),
            "explicit_recommendation_rate": rate(recommendations),
            "partner_article_citation_rate": rate(partner_citations),
            "competitors": {k: {**v, "mention_rate": rate(v["mentions"]), "recommendation_rate": rate(v["recommendations"])} for k, v in competitor_stats.items()},
        })
    return result
