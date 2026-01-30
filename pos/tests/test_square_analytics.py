import unittest
from unittest.mock import MagicMock, patch
from decimal import Decimal
from django.utils import timezone
from datetime import datetime, timedelta
import sys
import os

# Add the project root to sys.path to allow imports
sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')

# Mock Django settings BEFORE importing anything that uses it
from unittest.mock import patch
mock_settings = MagicMock()
mock_settings.SQUARE_ENV = "sandbox"
mock_settings.SQUARE_API_VERSION = "2024-01-18"

with patch('django.conf.settings', mock_settings):
    from pos.integrations import SquareIntegration

class TestSquareAnalytics(unittest.TestCase):
    def setUp(self):
        self.mock_restaurant = MagicMock()
        self.mock_restaurant.pos_location_id = "test_location"
        self.mock_restaurant.currency = "USD"
        self.mock_restaurant.get_square_access_token.return_value = "test_token"
        self.integration = SquareIntegration(self.mock_restaurant)

    def test_get_daily_sales_summary(self):
        # Mock sync_orders to return some test orders
        mock_orders = [
            {
                "total_money": {"amount": 10000}, # 100.00
                "total_tip_money": {"amount": 2000}, # 20.00
            },
            {
                "total_money": {"amount": 5000}, # 50.00
                "total_tip_money": {"amount": 500}, # 5.00
            }
        ]
        
        with patch.object(SquareIntegration, 'sync_orders', return_value=mock_orders):
            result = self.integration.get_daily_sales_summary()
            
            self.assertTrue(result["success"])
            self.assertEqual(result["total_revenue"], 150.00)
            self.assertEqual(result["total_orders"], 2)
            self.assertEqual(result["total_tips"], 25.00)

    def test_get_top_selling_items(self):
        mock_orders = [
            {
                "line_items": [
                    {"name": "Burger", "quantity": "2"},
                    {"name": "Fries", "quantity": "1"},
                ]
            },
            {
                "line_items": [
                    {"name": "Burger", "quantity": "1"},
                    {"name": "Coke", "quantity": "3"},
                ]
            }
        ]
        
        with patch.object(SquareIntegration, 'sync_orders', return_value=mock_orders):
            result = self.integration.get_top_selling_items()
            
            self.assertTrue(result["success"])
            top_items = {item["name"]: item["quantity"] for item in result["top_items"]}
            self.assertEqual(top_items["Burger"], 3)
            self.assertEqual(top_items["Coke"], 3)
            self.assertEqual(top_items["Fries"], 1)

if __name__ == '__main__':
    unittest.main()
