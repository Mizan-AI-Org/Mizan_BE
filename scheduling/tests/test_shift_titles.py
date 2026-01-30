import unittest
from datetime import datetime, time
from unittest.mock import MagicMock, patch
import sys

# Mock django before importing from scheduling
sys.modules['django'] = MagicMock()
sys.modules['django.utils'] = MagicMock()
sys.modules['django.conf'] = MagicMock()
sys.modules['django.db'] = MagicMock()

# Instead of importing, I'll copy the logic or mock the dependencies
# But since I want to test the actual code in the file, I'll try to mock the specific import

def mock_localtime(dt):
    return dt

class TestShiftTitles(unittest.TestCase):
    @patch('django.utils.timezone.localtime', side_effect=mock_localtime)
    def test_logic(self, mock_lt):
        # I'll manually import the functions after patching
        from scheduling.shift_auto_templates import detect_shift_context, generate_shift_title
        
        restaurant = MagicMock()
        restaurant.general_settings = {
            'peak_periods': {
                'breakfast': {'start': '07:00', 'end': '10:00'},
                'lunch': {'start': '12:00', 'end': '14:30'},
                'dinner': {'start': '19:00', 'end': '22:00'}
            }
        }

        # 08:30 during breakfast peak
        dt = datetime(2026, 1, 30, 8, 30)
        ctx = detect_shift_context(shift_title=None, shift_notes=None, start_dt=dt, end_dt=dt, restaurant=restaurant)
        self.assertEqual(ctx, "BREAKFAST")

        # 13:00 during lunch peak
        dt = datetime(2026, 1, 30, 13, 0)
        ctx = detect_shift_context(shift_title=None, shift_notes=None, start_dt=dt, end_dt=dt, restaurant=restaurant)
        self.assertEqual(ctx, "LUNCH")

        # 20:00 during dinner peak
        dt = datetime(2026, 1, 30, 20, 0)
        ctx = detect_shift_context(shift_title=None, shift_notes=None, start_dt=dt, end_dt=dt, restaurant=restaurant)
        self.assertEqual(ctx, "DINNER")

        # Keyword priority
        dt = datetime(2026, 1, 30, 8, 30)
        ctx = detect_shift_context(shift_title=None, shift_notes="Lock up the bar", start_dt=dt, end_dt=dt, restaurant=restaurant)
        self.assertEqual(ctx, "CLOSING")

        # Title generation
        title = generate_shift_title(shift_context="DINNER", staff_role="WAITER")
        self.assertEqual(title, "Dinner Service – Front of House")

        title = generate_shift_title(shift_context="BREAKFAST", staff_role="SERVER", workspace_location="Bar")
        self.assertEqual(title, "Breakfast Service – Bar")

        title = generate_shift_title(shift_context="OPENING", staff_role="CASHIER", workspace_location="Café")
        self.assertEqual(title, "Opening Shift – Café")

if __name__ == '__main__':
    unittest.main()
