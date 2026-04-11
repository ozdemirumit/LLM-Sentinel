"""
Content policy / guardrails enforcement engine.

Evaluates request messages and responses against content policies
(system prompt injection, topic blocking, model restriction, etc.).
"""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from sqlalchemy import select

from db import ContentPolicyDB, get_db
from logger import get_logger
from models import (
    ContentPolicy,
    ContentPolicyCreate,
    ContentPolicyUpdate,
    PolicyEvaluationResult,
)

log = get_logger(__name__)


# ==========================================================================
# Request evaluation
# ==========================================================================

async def evaluate_request(
    messages: list[dict[str, Any]],
    model: str,
    client_id: str | None,
    db_session: Any = None,
) -> PolicyEvaluationResult:
    """
    Evaluate messages against all active content policies.
    Returns PolicyEvaluationResult with possibly modified messages.
    """
    policies = await _load_policies(client_id)
    if not policies:
        return PolicyEvaluationResult(allowed=True)

    result = PolicyEvaluationResult(allowed=True)
    modified = list(messages)

    for policy in policies:
        ptype = policy.policy_type
        config = policy.config_json or {}

        if ptype == "system_prompt_inject":
            prompt = config.get("prompt", "")
            if prompt:
                # Prepend system message
                modified = [{"role": "system", "content": prompt}] + [
                    m for m in modified if not (m.get("role") == "system" and m.get("content") == prompt)
                ]
                result.applied_policies.append(f"system_prompt_inject:{policy.name}")

        elif ptype == "system_prompt_enforce":
            prompt = config.get("prompt", "")
            allow_client = config.get("allow_client_system", False)
            if not allow_client:
                modified = [m for m in modified if m.get("role") != "system"]
            if prompt:
                modified = [{"role": "system", "content": prompt}] + modified
            result.applied_policies.append(f"system_prompt_enforce:{policy.name}")

        elif ptype == "topic_block":
            action = config.get("action", "reject")
            blocked_keywords = config.get("blocked_keywords", [])
            blocked_patterns = config.get("blocked_patterns", [])
            msg_text = config.get("message", "This topic is blocked by policy.")

            all_text = " ".join(
                m.get("content", "") for m in modified
                if isinstance(m.get("content"), str)
            ).lower()

            blocked = False
            for kw in blocked_keywords:
                if kw.lower() in all_text:
                    blocked = True
                    break
            if not blocked:
                for pat in blocked_patterns:
                    try:
                        if re.search(pat, all_text, re.IGNORECASE):
                            blocked = True
                            break
                    except re.error:
                        pass

            if blocked:
                if action == "reject":
                    result.allowed = False
                    result.reject_reason = msg_text
                    result.applied_policies.append(f"topic_block:{policy.name}")
                    return result
                elif action == "redact":
                    for i, m in enumerate(modified):
                        content = m.get("content", "")
                        if isinstance(content, str):
                            for kw in blocked_keywords:
                                content = re.sub(re.escape(kw), "[BLOCKED]", content, flags=re.IGNORECASE)
                            for pat in blocked_patterns:
                                try:
                                    content = re.sub(pat, "[BLOCKED]", content, flags=re.IGNORECASE)
                                except re.error:
                                    pass
                            modified[i] = {**m, "content": content}
                    result.applied_policies.append(f"topic_block_redact:{policy.name}")

        elif ptype == "model_restrict":
            allowed_models = config.get("allowed_models", [])
            deny_message = config.get("deny_message", "Model not allowed by policy.")
            if allowed_models:
                model_allowed = any(fnmatch.fnmatch(model, pat) for pat in allowed_models)
                if not model_allowed:
                    result.allowed = False
                    result.reject_reason = deny_message
                    result.applied_policies.append(f"model_restrict:{policy.name}")
                    return result

        elif ptype == "max_output_tokens":
            max_tokens = config.get("max_tokens")
            if max_tokens:
                result.applied_policies.append(f"max_output_tokens:{policy.name}:{max_tokens}")

    if modified != messages:
        result.modified_messages = modified

    return result


# ==========================================================================
# Response evaluation
# ==========================================================================

async def evaluate_response(
    response_text: str,
    client_id: str | None,
    db_session: Any = None,
) -> tuple[str, bool]:
    """
    Evaluate LLM response against output_filter policies.
    Returns (filtered_text, was_filtered).
    """
    policies = await _load_policies(client_id)
    was_filtered = False

    for policy in policies:
        if policy.policy_type != "output_filter":
            continue

        config = policy.config_json or {}
        action = config.get("action", "redact")

        if config.get("check_pii"):
            # Basic PII patterns
            pii_patterns = [
                (r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b", "[EMAIL]"),
                (r"\b\d{3}-\d{2}-\d{4}\b", "[SSN]"),
                (r"\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14})\b", "[CARD]"),
            ]
            for pat, repl in pii_patterns:
                new_text, count = re.subn(pat, repl, response_text)
                if count > 0:
                    response_text = new_text
                    was_filtered = True

        if config.get("check_profanity"):
            profanity_words = config.get("profanity_list", [])
            for word in profanity_words:
                if word.lower() in response_text.lower():
                    response_text = re.sub(
                        re.escape(word), "[FILTERED]", response_text, flags=re.IGNORECASE
                    )
                    was_filtered = True

    return response_text, was_filtered


# ==========================================================================
# Policy loading
# ==========================================================================

async def _load_policies(client_id: str | None) -> list[ContentPolicy]:
    """Load active policies applicable to the given client, sorted by priority."""
    async with get_db() as db:
        result = await db.execute(
            select(ContentPolicyDB)
            .where(ContentPolicyDB.is_active == True)
            .order_by(ContentPolicyDB.priority)
        )
        rows = result.scalars().all()

    policies = []
    for r in rows:
        # Check client applicability
        applies = r.applies_to_clients
        if applies is not None and client_id and client_id not in applies:
            continue
        policies.append(ContentPolicy(
            id=r.id, name=r.name, policy_type=r.policy_type,
            config_json=r.config_json, is_active=r.is_active,
            applies_to_clients=r.applies_to_clients,
            priority=r.priority, created_at=r.created_at,
        ))
    return policies


# ==========================================================================
# CRUD
# ==========================================================================

async def get_all_policies() -> list[ContentPolicy]:
    async with get_db() as db:
        result = await db.execute(select(ContentPolicyDB).order_by(ContentPolicyDB.priority))
        rows = result.scalars().all()
    return [ContentPolicy(id=r.id, name=r.name, policy_type=r.policy_type,
                          config_json=r.config_json, is_active=r.is_active,
                          applies_to_clients=r.applies_to_clients,
                          priority=r.priority, created_at=r.created_at) for r in rows]


async def create_policy(data: ContentPolicyCreate) -> ContentPolicy:
    async with get_db() as db:
        row = ContentPolicyDB(
            name=data.name, policy_type=data.policy_type,
            config_json=data.config_json, applies_to_clients=data.applies_to_clients,
            priority=data.priority,
        )
        db.add(row)
        await db.flush()
        return ContentPolicy(id=row.id, name=row.name, policy_type=row.policy_type,
                             config_json=row.config_json, is_active=row.is_active,
                             applies_to_clients=row.applies_to_clients,
                             priority=row.priority, created_at=row.created_at)


async def update_policy(policy_id: str, data: ContentPolicyUpdate) -> ContentPolicy | None:
    async with get_db() as db:
        result = await db.execute(select(ContentPolicyDB).where(ContentPolicyDB.id == policy_id))
        row = result.scalars().first()
        if not row:
            return None
        if data.name is not None:
            row.name = data.name
        if data.config_json is not None:
            row.config_json = data.config_json
        if data.is_active is not None:
            row.is_active = data.is_active
        if data.applies_to_clients is not None:
            row.applies_to_clients = data.applies_to_clients
        if data.priority is not None:
            row.priority = data.priority
        await db.flush()
        return ContentPolicy(id=row.id, name=row.name, policy_type=row.policy_type,
                             config_json=row.config_json, is_active=row.is_active,
                             applies_to_clients=row.applies_to_clients,
                             priority=row.priority, created_at=row.created_at)


async def delete_policy(policy_id: str) -> bool:
    async with get_db() as db:
        result = await db.execute(select(ContentPolicyDB).where(ContentPolicyDB.id == policy_id))
        row = result.scalars().first()
        if not row:
            return False
        await db.delete(row)
    return True


async def test_policy(
    policy_id: str,
    test_messages: list[dict[str, Any]],
    test_model: str = "test-model",
) -> PolicyEvaluationResult:
    """Test a single policy against sample messages."""
    async with get_db() as db:
        result = await db.execute(select(ContentPolicyDB).where(ContentPolicyDB.id == policy_id))
        row = result.scalars().first()
    if not row:
        return PolicyEvaluationResult(allowed=False, reject_reason="Policy not found")

    # Temporarily create a single-policy list
    policy = ContentPolicy(
        id=row.id, name=row.name, policy_type=row.policy_type,
        config_json=row.config_json, is_active=True,
        applies_to_clients=None, priority=row.priority,
    )
    return await _evaluate_single(policy, test_messages, test_model)


async def _evaluate_single(
    policy: ContentPolicy,
    messages: list[dict],
    model: str,
) -> PolicyEvaluationResult:
    """Evaluate a single policy (for testing)."""
    # Reuse the main evaluate_request logic by temporarily creating a DB row
    # Instead, just inline the logic for the single policy
    result = PolicyEvaluationResult(allowed=True)
    config = policy.config_json or {}
    ptype = policy.policy_type

    if ptype == "topic_block":
        blocked_keywords = config.get("blocked_keywords", [])
        all_text = " ".join(m.get("content", "") for m in messages if isinstance(m.get("content"), str)).lower()
        for kw in blocked_keywords:
            if kw.lower() in all_text:
                result.allowed = False
                result.reject_reason = config.get("message", "Topic blocked")
                result.applied_policies.append(policy.name)
                return result

    elif ptype == "model_restrict":
        allowed_models = config.get("allowed_models", [])
        if allowed_models and not any(fnmatch.fnmatch(model, p) for p in allowed_models):
            result.allowed = False
            result.reject_reason = config.get("deny_message", "Model not allowed")
            result.applied_policies.append(policy.name)

    elif ptype == "system_prompt_inject":
        result.modified_messages = [{"role": "system", "content": config.get("prompt", "")}] + messages
        result.applied_policies.append(policy.name)

    return result
