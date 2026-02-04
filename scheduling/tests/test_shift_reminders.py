import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch
import sys
import os

# Mock django before importing
sys.modules['django'] = MagicMock()
sys.modules['django.utils'] = MagicMock()
sys.modules['django.conf'] = MagicMock()
sys.modules['django.db'] = MagicMock()
sys.modules['celery'] = MagicMock()

# Mock specific django modules
mock_timezone = MagicMock()
sys.modules['django.utils.timezone'] = mock_timezone
mock_timezone.now.return_value = datetime(2026, 1, 30, 12, 0)
mock_timezone.localtime.side_effect = lambda x: x

class TestTwoStageReminders(unittest.TestCase):
    @patch('scheduling.models.AssignedShift.objects.filter')
    @patch('notifications.services.NotificationService')
    def test_send_shift_reminders_30min(self, MockService, mock_filter):
        from scheduling.reminder_tasks import send_shift_reminders_30min
        
        mock_shift = MagicMock()
        mock_shift.id = "123"
        mock_shift.staff.phone = "+1234567890"
        
        mock_filter.return_value.select_related.return_value = [mock_shift]
        
        service_instance = MockService.return_value
        send_shift_reminders_30min()
        
        service_instance.send_shift_notification.assert_called_once_with(mock_shift, notification_type='SHIFT_REMINDER')
        self.assertTrue(mock_shift.shift_reminder_sent)

    @patch('scheduling.models.AssignedShift.objects.filter')
    @patch('notifications.services.NotificationService')
    def test_send_clock_in_reminders_10min(self, MockService, mock_filter):
        from scheduling.reminder_tasks import send_clock_in_reminders
        
        mock_shift = MagicMock()
        mock_shift.id = "456"
        mock_shift.staff.phone = "+1234567890"
        
        mock_filter.return_value.select_related.return_value = [mock_shift]
        
        service_instance = MockService.return_value
        send_clock_in_reminders()
        
        service_instance.send_shift_notification.assert_called_once_with(mock_shift, notification_type='CLOCK_IN_REMINDER')
        self.assertTrue(mock_shift.clock_in_reminder_sent)

    @patch('notifications.services.whatsapp_language_code', return_value='en_US')
    @patch('notifications.services.get_effective_language', return_value='en')
    @patch('notifications.services.tr', return_value='test message')
    @patch('notifications.services.Notification.objects.create')
    @patch('notifications.services.requests.post')
    def test_notification_service_logic(self, mock_post, mock_notif_create, mock_tr, mock_lang, mock_wa_lang):
        from notifications.services import NotificationService
        
        service = NotificationService()
        shift = MagicMock()
        shift.staff.first_name = "Hamza"
        shift.staff.phone = "+1234567890"
        shift.workspace_location = "Kitchen"
        shift.notes = "Lunch Service"
        shift.get_shift_duration_hours.return_value = 8.0
        
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = {"messages": [{"id": "wa_id"}]}
        
        # 1. Test T-1hr Shift Reminder (Template)
        service.send_shift_notification(shift, notification_type='SHIFT_REMINDER')
        self.assertTrue(mock_post.called)
        payload = mock_post.call_args_list[0].kwargs['json']
        self.assertEqual(payload['template']['name'], 'clock_in_reminder')
        
        mock_post.reset_mock()
        
        # 2. Test T-10 Clock-In Reminder (Buttons)
        service.send_shift_notification(shift, notification_type='CLOCK_IN_REMINDER')
        self.assertTrue(mock_post.called)
        payload = mock_post.call_args_list[0].kwargs['json']
        self.assertEqual(payload['type'], 'interactive')
        self.assertEqual(payload['interactive']['action']['buttons'][0]['reply']['id'], 'clock_in_now')

if __name__ == '__main__':
    unittest.main()
