from typing import List, Optional, Tuple

from bson import ObjectId
from rapidfuzz import fuzz

from tahmeed.models.keyword_rule import KeywordRule
from tahmeed.services.api_client import api_client
from tahmeed.services.api_models import desktop_document, get_all_pages


def _rule(document: dict) -> KeywordRule:
    return KeywordRule.from_doc(desktop_document(document))


async def get_all_rules(include_inactive: bool = False) -> List[KeywordRule]:
    documents = await get_all_pages(
        "v1/keyword-rules", params={"include_inactive": include_inactive}
    )
    rules = [_rule(document) for document in documents]
    return sorted(rules, key=lambda rule: rule.priority, reverse=True)


async def create_rule(
    pattern: str,
    category_id: ObjectId,
    category_name: str,
    priority: int,
    match_type: str,
) -> KeywordRule:
    document = await api_client.request(
        "POST",
        "v1/keyword-rules",
        json={
            "pattern": pattern,
            "category_id": str(category_id),
            "category_name": category_name,
            "priority": priority,
            "match_type": match_type,
        },
    )
    return _rule(document)


async def update_rule(rule_id: ObjectId, **fields) -> None:
    if "category_id" in fields:
        fields["category_id"] = str(fields["category_id"])
    await api_client.request(
        "PATCH", f"v1/keyword-rules/{rule_id}", json={"values": fields}
    )


async def toggle_rule(rule_id: ObjectId, active: bool) -> None:
    await update_rule(rule_id, active=active)


async def test_description(
    description: str,
) -> Optional[Tuple[str, ObjectId, float]]:
    rules = await get_all_rules()
    text = description.lower().strip()
    for rule in rules:
        pattern = rule.pattern.lower().strip()
        if rule.match_type == "exact":
            score = 1.0 if text == pattern else 0.0
        elif rule.match_type == "startswith":
            score = 1.0 if text.startswith(pattern) else 0.0
        elif rule.match_type == "contains":
            score = 1.0 if pattern in text else 0.0
        elif rule.match_type == "fuzzy":
            score = fuzz.partial_ratio(pattern, text) / 100.0
        else:
            score = 0.0
        if score >= 0.75:
            return rule.category_name, rule.category_id, score
    return None
