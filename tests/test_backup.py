"""Tests for backup functionality."""

import pytest
from backup import list_backups


class TestBackup:
    def test_list_backups_empty(self):
        bl = list_backups()
        assert isinstance(bl, list)

    async def test_backup_endpoint_admin_only(self, unauth_client):
        r = await unauth_client.get("/v1/admin/backup/list")
        assert r.status_code == 401

    async def test_backup_list_endpoint(self, client):
        r = await client.get("/v1/admin/backup/list")
        assert r.status_code == 200
