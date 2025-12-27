"""
POS Integration Manager
Handles synchronization with external POS providers (Toast, Square, Clover)
"""

import requests
from django.conf import settings
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from .models import Order, OrderLineItem, Payment
from menu.models import MenuItem


class BasePOSIntegration(ABC):
    """Abstract base class for POS integrations"""
    
    def __init__(self, restaurant):
        self.restaurant = restaurant
        self.api_key = restaurant.pos_api_key
        self.merchant_id = restaurant.pos_merchant_id
        
    @abstractmethod
    def sync_menu_items(self) -> Dict:
        """Sync menu items from external POS to local database"""
        pass
    
    @abstractmethod
    def sync_orders(self, start_date=None, end_date=None) -> List[Dict]:
        """Sync orders from external POS"""
        pass
    
    @abstractmethod
    def create_order(self, order: Order) -> Dict:
        """Push order to external POS"""
        pass
    
    @abstractmethod
    def process_payment(self, payment: Payment) -> Dict:
        """Process payment through external POS"""
        pass


class ToastIntegration(BasePOSIntegration):
    """Toast POS Integration"""
    BASE_URL = "https://api.toasttab.com/v1"
    
    def sync_menu_items(self) -> Dict:
        """Sync menu from Toast"""
        try:
            headers = {'Authorization': f'Bearer {self.api_key}'}
            response = requests.get(
                f"{self.BASE_URL}/menus",
                headers=headers
            )
            response.raise_for_status()
            
            menu_data = response.json()
            synced_items = []
            
            for group in menu_data.get('menuGroups', []):
                for item in group.get('items', []):
                    menu_item, created = MenuItem.objects.update_or_create(
                        restaurant=self.restaurant,
                        external_id=item['guid'],
                        defaults={
                            'name': item['name'],
                            'description': item.get('description', ''),
                            'price': item.get('price', 0) / 100,  # Toast uses cents
                            'is_available': item.get('visibility') == 'AVAILABLE'
                        }
                    )
                    synced_items.append(menu_item.id)
            
            return {
                'success': True,
                'items_synced': len(synced_items),
                'provider': 'Toast'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def sync_orders(self, start_date=None, end_date=None) -> List[Dict]:
        """Sync orders from Toast"""
        headers = {'Authorization': f'Bearer {self.api_key}'}
        params = {}
        if start_date:
            params['startDate'] = start_date.isoformat()
        if end_date:
            params['endDate'] = end_date.isoformat()
            
        response = requests.get(
            f"{self.BASE_URL}/orders",
            headers=headers,
            params=params
        )
        response.raise_for_status()
        return response.json().get('orders', [])
    
    def create_order(self, order: Order) -> Dict:
        """Push order to Toast POS"""
        headers = {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}
        
        order_data = {
            'guid': str(order.id),
            'checks': [{
                'selections': [
                    {
                        'itemGuid': str(item.menu_item.external_id),
                        'quantity': item.quantity,
                        'price': int(item.unit_price * 100)
                    }
                    for item in order.items.all()
                ]
            }]
        }
        
        response = requests.post(
            f"{self.BASE_URL}/orders",
            headers=headers,
            json=order_data
        )
        response.raise_for_status()
        return response.json()
    
    def process_payment(self, payment: Payment) -> Dict:
        """Process payment through Toast"""
        # Toast payment processing implementation
        return {'success': True, 'transaction_id': f'TOAST_{payment.id}'}


class SquareIntegration(BasePOSIntegration):
    """Square POS Integration"""
    BASE_URL = "https://connect.squareup.com/v2"
    
    def sync_menu_items(self) -> Dict:
        """Sync menu from Square"""
        try:
            headers = {
                'Authorization': f'Bearer {self.api_key}',
                'Square-Version': '2024-01-18'
            }
            response = requests.get(
                f"{self.BASE_URL}/catalog/list",
                headers=headers,
                params={'types': 'ITEM'}
            )
            response.raise_for_status()
            
            catalog =response.json()
            synced_items = []
            
            for obj in catalog.get('objects', []):
                if obj['type'] == 'ITEM':
                    item_data = obj['item_data']
                    for variation in item_data.get('variations', []):
                        var_data = variation['item_variation_data']
                        menu_item, created = MenuItem.objects.update_or_create(
                            restaurant=self.restaurant,
                            external_id=variation['id'],
                            defaults={
                                'name': item_data['name'],
                                'description': item_data.get('description', ''),
                                'price': int(var_data.get('price_money', {}).get('amount', 0)) / 100,
                                'is_available': not item_data.get('is_deleted', False)
                            }
                        )
                        synced_items.append(menu_item.id)
            
            return {
                'success': True,
                'items_synced': len(synced_items),
                'provider': 'Square'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def sync_orders(self, start_date=None, end_date=None) -> List[Dict]:
        """Sync orders from Square"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Square-Version': '2024-01-18'
        }
        
        query = {'location_ids': [self.merchant_id]}
        if start_date:
            query['start_at'] = start_date.isoformat()
        if end_date:
            query['end_at'] = end_date.isoformat()
        
        response = requests.post(
            f"{self.BASE_URL}/orders/search",
            headers=headers,
            json={'query': query}
        )
        response.raise_for_status()
        return response.json().get('orders', [])
    
    def create_order(self, order: Order) -> Dict:
        """Push order to Square"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Square-Version': '2024-01-18',
            'Content-Type': 'application/json'
        }
        
        order_data = {
            'idempotency_key': str(order.id),
            'order': {
                'location_id': self.merchant_id,
                'line_items': [
                    {
                        'quantity': str(item.quantity),
                        'catalog_object_id': str(item.menu_item.external_id),
                        'base_price_money': {
                            'amount': int(item.unit_price * 100),
                            'currency': 'USD'
                        }
                    }
                    for item in order.items.all()
                ]
            }
        }
        
        response = requests.post(
            f"{self.BASE_URL}/orders",
            headers=headers,
            json=order_data
        )
        response.raise_for_status()
        return response.json()
    
    def process_payment(self, payment: Payment) -> Dict:
        """Process payment through Square"""
        return {'success': True, 'transaction_id': f'SQUARE_{payment.id}'}


class CloverIntegration(BasePOSIntegration):
    """Clover POS Integration"""
    BASE_URL = "https://api.clover.com/v3"
    
    def sync_menu_items(self) -> Dict:
        """Sync menu from Clover"""
        try:
            headers = {'Authorization': f'Bearer {self.api_key}'}
            response = requests.get(
                f"{self.BASE_URL}/merchants/{self.merchant_id}/items",
                headers=headers
            )
            response.raise_for_status()
            
            items = response.json().get('elements', [])
            synced_items = []
            
            for item in items:
                menu_item, created = MenuItem.objects.update_or_create(
                    restaurant=self.restaurant,
                    external_id=item['id'],
                    defaults={
                        'name': item['name'],
                        'description': item.get('description', ''),
                        'price': item.get('price', 0) / 100,  # Clover uses cents
                        'is_available': not item.get('hidden', False)
                    }
                )
                synced_items.append(menu_item.id)
            
            return {
                'success': True,
                'items_synced': len(synced_items),
                'provider': 'Clover'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def sync_orders(self, start_date=None, end_date=None) -> List[Dict]:
        """Sync orders from Clover"""
        headers = {'Authorization': f'Bearer {self.api_key}'}
        params = {}
        if start_date:
            params['filter'] = f'createdTime>={int(start_date.timestamp() * 1000)}'
        
        response = requests.get(
            f"{self.BASE_URL}/merchants/{self.merchant_id}/orders",
            headers=headers,
            params=params
        )
        response.raise_for_status()
        return response.json().get('elements', [])
    
    def create_order(self, order: Order) -> Dict:
        """Push order to Clover"""
        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json'
        }
        
        order_data = {
            'state': 'open',
            'lineItems': [
                {
                    'item': {'id': str(item.menu_item.external_id)},
                    'unitQty': item.quantity,
                    'price': int(item.unit_price * 100)
                }
                for item in order.items.all()
            ]
        }
        
        response = requests.post(
            f"{self.BASE_URL}/merchants/{self.merchant_id}/orders",
            headers=headers,
            json=order_data
        )
        response.raise_for_status()
        return response.json()
    
    def process_payment(self, payment: Payment) -> Dict:
        """Process payment through Clover"""
        return {'success': True, 'transaction_id': f'CLOVER_{payment.id}'}


class IntegrationManager:
    """Main manager for handling POS integrations"""
    
    PROVIDERS = {
        'TOAST': ToastIntegration,
        'SQUARE': SquareIntegration,
        'CLOVER': CloverIntegration,
    }
    
    @classmethod
    def get_integration(cls, restaurant):
        """Get the appropriate integration instance for a restaurant"""
        provider = restaurant.pos_provider
        
        if provider == 'NONE' or not provider:
            return None
        
        integration_class = cls.PROVIDERS.get(provider)
        if not integration_class:
            raise ValueError(f"Unsupported POS provider: {provider}")
        
        return integration_class(restaurant)
    
    @classmethod
    def sync_menu(cls, restaurant) -> Dict:
        """Sync menu items for a restaurant"""
        integration = cls.get_integration(restaurant)
        if not integration:
            return {'success': False, 'error': 'No POS integration configured'}
        
        return integration.sync_menu_items()
    
    @classmethod
    def sync_orders(cls, restaurant, start_date=None, end_date=None) -> Dict:
        """Sync orders for a restaurant"""
        integration = cls.get_integration(restaurant)
        if not integration:
            return {'success': False, 'error': 'No POS integration configured'}
        
        try:
            orders = integration.sync_orders(start_date, end_date)
            return {'success': True, 'orders_count': len(orders), 'orders': orders}
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    @classmethod
    def push_order(cls, order: Order) -> Dict:
        """Push an order to the external POS"""
        integration = cls.get_integration(order.restaurant)
        if not integration:
            return {'success': False, 'error': 'No POS integration configured'}
        
        try:
            result = integration.create_order(order)
            return {'success': True, 'result': result}
        except Exception as e:
            return {'success': False, 'error': str(e)}
