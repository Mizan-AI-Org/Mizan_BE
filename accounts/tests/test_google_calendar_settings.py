"""Google Calendar OAuth settings must load from .env via Django settings."""

from django.test import RequestFactory, SimpleTestCase, override_settings

from accounts.views_onboarding import OnboardingGoogleCalendarView
from dashboard.api.meetings_reminders import MeetingsRemindersView


class GoogleCalendarSettingsTests(SimpleTestCase):
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="test-client-id.apps.googleusercontent.com",
        GOOGLE_OAUTH_CLIENT_SECRET="test-secret",
        GOOGLE_OAUTH_REDIRECT_URI="https://api.example.com/api/integrations/google-calendar/callback/",
        DEBUG=False,
    )
    def test_creds_and_redirect_from_settings(self):
        client_id, client_secret = OnboardingGoogleCalendarView._creds()
        self.assertEqual(client_id, "test-client-id.apps.googleusercontent.com")
        self.assertEqual(client_secret, "test-secret")
        self.assertTrue(MeetingsRemindersView._server_configured())

        rf = RequestFactory()
        req = rf.post(
            "/api/integrations/google-calendar/",
            SERVER_NAME="internal",
            SERVER_PORT=8000,
        )
        self.assertEqual(
            OnboardingGoogleCalendarView._redirect_uri(req),
            "https://api.example.com/api/integrations/google-calendar/callback/",
        )

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="",
        GOOGLE_OAUTH_CLIENT_SECRET="",
    )
    def test_not_configured_when_empty(self):
        self.assertFalse(MeetingsRemindersView._server_configured())
