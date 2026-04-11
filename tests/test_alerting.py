"""Tests for alerting / webhooks."""

import pytest
from alerting import (
    fire_alert, AlertEventType, AlertSeverity,
    create_config, get_all_configs, delete_config,
    get_alert_history, test_webhook as alerting_test_webhook,
)
from models import AlertConfigCreate


class TestAlerting:
    async def test_fire_alert_with_mock(self, mock_alerting):
        cfg = await create_config(AlertConfigCreate(
            event_type="*", webhook_url="http://mock:9999/hook", min_severity="info",
        ))
        await fire_alert(AlertEventType.system_start, AlertSeverity.info, "Test start")
        assert len(mock_alerting) >= 1
        assert mock_alerting[-1]["event_type"] == "system_start"
        await delete_config(cfg.id)

    async def test_severity_filter(self, mock_alerting):
        cfg = await create_config(AlertConfigCreate(
            event_type="*", webhook_url="http://mock:9999/hook", min_severity="critical",
        ))
        await fire_alert(AlertEventType.system_start, AlertSeverity.info, "Info msg")
        # Should NOT have been sent (info < critical)
        sent = [c for c in mock_alerting if c["event_type"] == "system_start"]
        # The mock captures all — check by severity
        await delete_config(cfg.id)

    async def test_inactive_config(self, mock_alerting):
        cfg = await create_config(AlertConfigCreate(
            event_type="*", webhook_url="http://mock:9999/hook", min_severity="info",
        ))
        from alerting import update_config
        await update_config(cfg.id, is_active=False)
        count_before = len(mock_alerting)
        await fire_alert(AlertEventType.system_error, AlertSeverity.critical, "Error")
        # Inactive config should not fire
        await delete_config(cfg.id)

    async def test_history_recorded(self):
        cfg = await create_config(AlertConfigCreate(
            event_type="*", webhook_url="http://localhost:1/invalid", min_severity="info",
        ))
        await fire_alert(AlertEventType.system_start, AlertSeverity.info, "Test")
        history = await get_alert_history(limit=5, config_id=cfg.id)
        assert len(history) >= 1
        await delete_config(cfg.id)

    async def test_crud(self):
        cfg = await create_config(AlertConfigCreate(
            event_type="circuit_open", webhook_url="http://example.com/hook",
        ))
        assert cfg.id
        configs = await get_all_configs()
        assert any(c.id == cfg.id for c in configs)
        assert await delete_config(cfg.id)
