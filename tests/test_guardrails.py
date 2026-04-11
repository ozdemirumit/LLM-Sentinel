"""Tests for content policy / guardrails."""

import pytest
from guardrails import (
    evaluate_request, evaluate_response,
    create_policy, get_all_policies, update_policy, delete_policy,
)
from models import ContentPolicyCreate, ContentPolicyUpdate


class TestSystemPromptInject:
    async def test_inject(self):
        pol = await create_policy(ContentPolicyCreate(
            name="inject-test", policy_type="system_prompt_inject",
            config_json={"prompt": "You are a helpful assistant."}, priority=1,
        ))
        try:
            result = await evaluate_request(
                [{"role": "user", "content": "Hi"}], "gpt-4o", None
            )
            assert result.allowed
            assert result.modified_messages is not None
            assert result.modified_messages[0]["content"] == "You are a helpful assistant."
        finally:
            await delete_policy(pol.id)


class TestSystemPromptEnforce:
    async def test_enforce(self):
        pol = await create_policy(ContentPolicyCreate(
            name="enforce-test", policy_type="system_prompt_enforce",
            config_json={"prompt": "Enforced system prompt.", "allow_client_system": False},
            priority=1,
        ))
        try:
            result = await evaluate_request(
                [{"role": "system", "content": "Client system"}, {"role": "user", "content": "Hi"}],
                "gpt-4o", None,
            )
            assert result.allowed
            assert result.modified_messages is not None
            # Client system should be removed
            system_msgs = [m for m in result.modified_messages if m["role"] == "system"]
            assert len(system_msgs) == 1
            assert system_msgs[0]["content"] == "Enforced system prompt."
        finally:
            await delete_policy(pol.id)


class TestTopicBlock:
    async def test_reject(self):
        pol = await create_policy(ContentPolicyCreate(
            name="block-test", policy_type="topic_block",
            config_json={"blocked_keywords": ["forbidden"], "action": "reject", "message": "Topic blocked."},
            priority=1,
        ))
        try:
            result = await evaluate_request(
                [{"role": "user", "content": "Tell me about forbidden stuff"}], "gpt-4o", None,
            )
            assert not result.allowed
            assert result.reject_reason == "Topic blocked."
        finally:
            await delete_policy(pol.id)

    async def test_redact(self):
        pol = await create_policy(ContentPolicyCreate(
            name="redact-test", policy_type="topic_block",
            config_json={"blocked_keywords": ["secret"], "action": "redact"},
            priority=1,
        ))
        try:
            result = await evaluate_request(
                [{"role": "user", "content": "The secret plan"}], "gpt-4o", None,
            )
            assert result.allowed
            assert result.modified_messages is not None
            assert "[BLOCKED]" in result.modified_messages[0]["content"]
        finally:
            await delete_policy(pol.id)


class TestModelRestrict:
    async def test_disallowed(self):
        pol = await create_policy(ContentPolicyCreate(
            name="restrict-test", policy_type="model_restrict",
            config_json={"allowed_models": ["gpt-4o-mini"], "deny_message": "Model not allowed."},
            priority=1,
        ))
        try:
            result = await evaluate_request([], "gpt-4o", None)
            assert not result.allowed
            assert result.reject_reason == "Model not allowed."
        finally:
            await delete_policy(pol.id)

    async def test_glob_match(self):
        pol = await create_policy(ContentPolicyCreate(
            name="glob-test", policy_type="model_restrict",
            config_json={"allowed_models": ["gpt-4*"]}, priority=1,
        ))
        try:
            result = await evaluate_request([], "gpt-4o", None)
            assert result.allowed
        finally:
            await delete_policy(pol.id)


class TestOutputFilter:
    async def test_pii_email(self):
        pol = await create_policy(ContentPolicyCreate(
            name="pii-test", policy_type="output_filter",
            config_json={"check_pii": True}, priority=1,
        ))
        try:
            filtered, was = await evaluate_response("Contact me at user@example.com", None)
            assert "[EMAIL]" in filtered
            assert was
        finally:
            await delete_policy(pol.id)


class TestPolicyPriority:
    async def test_priority_order(self):
        p1 = await create_policy(ContentPolicyCreate(
            name="prio-50", policy_type="topic_block",
            config_json={"blocked_keywords": ["harmless"], "action": "reject", "message": "P50"},
            priority=50,
        ))
        p2 = await create_policy(ContentPolicyCreate(
            name="prio-1", policy_type="topic_block",
            config_json={"blocked_keywords": ["harmless"], "action": "reject", "message": "P1"},
            priority=1,
        ))
        try:
            result = await evaluate_request(
                [{"role": "user", "content": "harmless"}], "gpt-4o", None,
            )
            assert not result.allowed
            # Priority 1 should fire first
            assert result.reject_reason == "P1"
        finally:
            await delete_policy(p1.id)
            await delete_policy(p2.id)


class TestPolicyPerClient:
    async def test_applies_to_specific_client(self):
        pol = await create_policy(ContentPolicyCreate(
            name="client-specific", policy_type="topic_block",
            config_json={"blocked_keywords": ["restricted"], "action": "reject", "message": "Blocked"},
            applies_to_clients=["client-abc"],
            priority=1,
        ))
        try:
            # Should apply to client-abc
            r1 = await evaluate_request(
                [{"role": "user", "content": "restricted topic"}], "gpt-4o", "client-abc",
            )
            assert not r1.allowed
            # Should NOT apply to other clients
            r2 = await evaluate_request(
                [{"role": "user", "content": "restricted topic"}], "gpt-4o", "other-client",
            )
            assert r2.allowed
        finally:
            await delete_policy(pol.id)


class TestPolicyCRUD:
    async def test_crud_endpoints(self, client):
        r = await client.post("/v1/policies", json={
            "name": "ep-test", "policy_type": "topic_block",
            "config_json": {"blocked_keywords": ["test"], "action": "reject"},
            "priority": 99,
        })
        assert r.status_code == 200
        pid = r.json()["id"]

        r = await client.get(f"/v1/policies/{pid}")
        assert r.status_code == 200

        r = await client.put(f"/v1/policies/{pid}", json={"priority": 50})
        assert r.status_code == 200

        r = await client.delete(f"/v1/policies/{pid}")
        assert r.status_code == 200
