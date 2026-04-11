"""Tests for model aliasing."""

import pytest
from model_alias import (
    seed_builtin_aliases, get_all_aliases, get_alias,
    create_alias, update_alias, delete_alias, increment_usage,
)
from models import ModelAliasCreate, ModelAliasUpdate


class TestModelAlias:
    async def test_seed_aliases(self):
        count = await seed_builtin_aliases()
        assert count == 0  # Already seeded

    async def test_get_all(self):
        aliases = await get_all_aliases()
        assert len(aliases) >= 7

    async def test_get_alias_found(self):
        a = await get_alias("fast")
        assert a is not None
        assert a.provider == "anthropic"
        assert "haiku" in a.model

    async def test_get_alias_not_found(self):
        a = await get_alias("nonexistent-alias-xyz")
        assert a is None

    async def test_create_valid(self):
        a = await create_alias(ModelAliasCreate(
            alias="test-alias-1", provider="openai", model="gpt-4o",
        ))
        assert a.alias == "test-alias-1"
        # Cleanup
        await delete_alias(a.id)

    async def test_create_invalid_slug(self):
        with pytest.raises(ValueError, match="URL-safe"):
            await create_alias(ModelAliasCreate(
                alias="INVALID SLUG!", provider="openai", model="gpt-4o",
            ))

    async def test_create_duplicate(self):
        a = await create_alias(ModelAliasCreate(
            alias="dup-test-1", provider="openai", model="gpt-4o",
        ))
        with pytest.raises(ValueError, match="already exists"):
            await create_alias(ModelAliasCreate(
                alias="dup-test-1", provider="openai", model="gpt-4o",
            ))
        await delete_alias(a.id)

    async def test_update(self):
        a = await create_alias(ModelAliasCreate(
            alias="upd-test-1", provider="openai", model="gpt-4o",
        ))
        updated = await update_alias(a.id, ModelAliasUpdate(model="gpt-4o-mini"))
        assert updated is not None
        assert updated.model == "gpt-4o-mini"
        await delete_alias(a.id)

    async def test_delete(self):
        a = await create_alias(ModelAliasCreate(
            alias="del-test-1", provider="openai", model="gpt-4o",
        ))
        assert await delete_alias(a.id)
        assert await get_alias("del-test-1") is None

    async def test_increment_usage(self):
        a = await create_alias(ModelAliasCreate(
            alias="inc-test-1", provider="openai", model="gpt-4o",
        ))
        await increment_usage(a.id)
        updated = await get_alias("inc-test-1")
        assert updated is not None and updated.usage_count >= 1
        await delete_alias(a.id)
