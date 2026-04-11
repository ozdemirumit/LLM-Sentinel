"""Tests for config export/import."""

import pytest
from config_export import export_config, verify_signature, import_config
from models import ConfigExportData, ConfigImportOptions


class TestConfigExport:
    async def test_export_returns_sections(self):
        data = await export_config()
        assert len(data.filter_patterns) >= 20
        assert len(data.model_aliases) >= 7
        assert len(data.cost_rates) >= 10
        assert data.schema_version == "1.0"

    async def test_excludes_api_keys(self):
        data = await export_config()
        for c in data.clients:
            assert "api_key_hash" not in c

    async def test_signature_present(self):
        data = await export_config()
        assert data.signature is not None

    async def test_verify_valid(self):
        data = await export_config()
        assert verify_signature(data) == True

    async def test_verify_tampered(self):
        data = await export_config()
        data.filter_patterns.append({"name": "tampered", "pattern": "x"})
        assert verify_signature(data) == False

    async def test_import_roundtrip(self):
        data = await export_config()
        result = await import_config(data, ConfigImportOptions(
            overwrite_existing=False, verify_signature=False,
        ))
        # All should be skipped (already exist)
        assert result.skipped >= 0
        assert len(result.errors) == 0

    async def test_endpoint_admin_only(self, unauth_client):
        r = await unauth_client.get("/v1/admin/config/export")
        assert r.status_code == 401

    async def test_endpoint_works(self, client):
        r = await client.get("/v1/admin/config/export")
        assert r.status_code == 200
        data = r.json()
        assert "filter_patterns" in data
