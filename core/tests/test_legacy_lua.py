from django.test import SimpleTestCase, override_settings

from core.legacy_lua import (
    legacy_lua_enabled,
    legacy_lua_user_events_configured,
    legacy_lua_whatsapp_url,
)


class LegacyLuaSettingsTests(SimpleTestCase):
    @override_settings(LUA_LEGACY_ENABLED=False, LUA_WHATSAPP_WEBHOOK_URL="https://example.com/hook")
    def test_legacy_off_disables_whatsapp_forward(self):
        self.assertFalse(legacy_lua_enabled())
        self.assertEqual(legacy_lua_whatsapp_url(), "")
        self.assertFalse(legacy_lua_user_events_configured())

    @override_settings(
        LUA_LEGACY_ENABLED=True,
        LUA_WHATSAPP_WEBHOOK_URL="https://example.com/hook",
        LUA_USER_EVENTS_WEBHOOK="https://example.com/events",
    )
    def test_legacy_on_exposes_urls(self):
        self.assertTrue(legacy_lua_enabled())
        self.assertEqual(legacy_lua_whatsapp_url(), "https://example.com/hook")
        self.assertTrue(legacy_lua_user_events_configured())
