from typing import List, Optional, Tuple, Any
from bson import ObjectId
from rapidfuzz import fuzz

from tahmeed.db.connection import get_db
from tahmeed.models.keyword_rule import KeywordRule


async def get_all_rules(include_inactive: bool = False) -> List[KeywordRule]:
    db = get_db()
    query = {} if include_inactive else {"active": True}
    cursor = db.keyword_rules.find(query).sort("priority", -1)
    docs = await cursor.to_list(length=None)
    return [KeywordRule.from_doc(d) for d in docs]


async def create_rule(
    pattern: str,
    category_id: ObjectId,
    category_name: str,
    priority: int,
    match_type: str,
) -> KeywordRule:
    db = get_db()
    rule = KeywordRule(
        pattern=pattern,
        category_id=category_id,
        category_name=category_name,
        priority=priority,
        match_type=match_type,
    )
    result = await db.keyword_rules.insert_one(rule.to_doc())
    rule._id = result.inserted_id
    return rule


async def update_rule(rule_id: ObjectId, **fields) -> None:
    db = get_db()
    await db.keyword_rules.update_one({"_id": rule_id}, {"$set": fields})


async def toggle_rule(rule_id: ObjectId, active: bool) -> None:
    await update_rule(rule_id, active=active)


async def test_description(description: str) -> Optional[Tuple[str, ObjectId, float]]:
    """
    Run a description through all active rules in priority order.
    Returns (category_name, category_id, confidence 0.0–1.0) or None if no match.
    """
    rules = await get_all_rules()
    text = description.lower().strip()

    for rule in rules:  # already sorted priority desc
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
            return (rule.category_name, rule.category_id, score)

    return None
