"""
POS Integration Manager
Handles synchronization with external POS providers (Toast, Square, Clover)
"""

import requests
from django.conf import settings
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from .models import Order, OrderLineItem, Payment
from menu.models import MenuItem, MenuCategory
from django.utils import timezone
from datetime import timedelta
import time
import random
import uuid


class BasePOSIntegration(ABC):
    """Abstract base class for POS integrations"""
    
    def __init__(self, restaurant):
        self.restaurant = restaurant
        self.api_key = restaurant.pos_api_key
        self.merchant_id = restaurant.pos_merchant_id
        self.location_id = getattr(restaurant, "pos_location_id", None)
        
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
                        external_provider="TOAST",
                        external_id=item['guid'],
                        defaults={
                            'name': item['name'],
                            'description': item.get('description', ''),
                            'price': item.get('price', 0) / 100,  # Toast uses cents
                            'is_active': item.get('visibility') == 'AVAILABLE'
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
                    for item in order.line_items.all()
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
    def _base_url(self) -> str:
        env = getattr(settings, "SQUARE_ENV", "production")
        host = "https://connect.squareup.com" if env == "production" else "https://connect.squareupsandbox.com"
        return f"{host}/v2"

    def _square_version(self) -> str:
        return getattr(settings, "SQUARE_API_VERSION", "2024-01-18")

    def _oauth_base(self) -> str:
        env = getattr(settings, "SQUARE_ENV", "production")
        return "https://connect.squareup.com" if env == "production" else "https://connect.squareupsandbox.com"

    def _refresh_oauth_token(self) -> None:
        """Refresh Square OAuth access token when expiring (best-effort)."""
        refresh_token = self.restaurant.get_square_refresh_token()
        if not refresh_token:
            return
        if not getattr(settings, "SQUARE_APPLICATION_ID", ""):
            return
        # Server-side apps should use client_secret; keep optional for PKCE.
        payload = {
            "client_id": settings.SQUARE_APPLICATION_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "redirect_uri": getattr(settings, "SQUARE_REDIRECT_URI", ""),
        }
        if getattr(settings, "SQUARE_APPLICATION_SECRET", ""):
            payload["client_secret"] = settings.SQUARE_APPLICATION_SECRET

        try:
            resp = requests.post(f"{self._oauth_base()}/oauth2/token", json=payload, timeout=15)
            data = resp.json() if resp.content else {}
            resp.raise_for_status()
        except Exception:
            return

        access_token = data.get("access_token") or ""
        new_refresh = data.get("refresh_token") or ""
        expires_at = data.get("expires_at") or None
        try:
            from django.utils.dateparse import parse_datetime
            expires_dt = parse_datetime(expires_at) if isinstance(expires_at, str) else None
        except Exception:
            expires_dt = None

        sq = self.restaurant.get_square_oauth() or {}
        if access_token:
            sq["access_token"] = access_token
        if new_refresh:
            sq["refresh_token"] = new_refresh
        if expires_at:
            sq["expires_at"] = expires_at

        self.restaurant.set_square_oauth(sq)
        self.restaurant.pos_token_expires_at = expires_dt
        # Keep connected unless refresh fully fails
        self.restaurant.save(update_fields=["pos_oauth_data", "pos_token_expires_at"])

    def _auth_token(self) -> str:
        # Prefer OAuth token; fall back to legacy api_key field.
        # Refresh shortly before expiry (5 minutes).
        try:
            exp = getattr(self.restaurant, "pos_token_expires_at", None)
            if exp and timezone.now() >= (exp - timedelta(minutes=5)):
                self._refresh_oauth_token()
        except Exception:
            pass
        return self.restaurant.get_square_access_token()

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self._auth_token()}",
            "Square-Version": self._square_version(),
        }

    def _request(self, method: str, path: str, *, params=None, json=None, timeout=20, max_retries=5):
        url = f"{self._base_url()}{path}"
        for attempt in range(max_retries):
            resp = requests.request(method, url, headers=self._headers(), params=params, json=json, timeout=timeout)
            # Rate limit handling
            if resp.status_code == 429:
                delay = min(1 * (2 ** attempt), 30)
                jitter = random.uniform(0, delay * 0.1)
                time.sleep(delay + jitter)
                continue
            # Token expiry/revocation surfaces as 401; mark disconnected best-effort
            if resp.status_code == 401:
                try:
                    self.restaurant.pos_is_connected = False
                    self.restaurant.save(update_fields=["pos_is_connected"])
                except Exception:
                    pass
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
    
    def sync_menu_items(self) -> Dict:
        """Sync menu from Square"""
        try:
            response = self._request(
                "GET",
                "/catalog/list",
                params={"types": "ITEM,CATEGORY"},
            )
            catalog = response.json() or {}
            synced_items = []
            synced_categories = 0

            # Categories
            categories_by_id = {}
            for obj in catalog.get("objects", []) or []:
                if obj.get("type") == "CATEGORY":
                    cat_data = obj.get("category_data") or {}
                    name = cat_data.get("name") or "Uncategorized"
                    category, _ = MenuCategory.objects.update_or_create(
                        restaurant=self.restaurant,
                        external_provider="SQUARE",
                        external_id=obj.get("id"),
                        defaults={
                            "name": name,
                            "description": cat_data.get("description") or "",
                            "is_active": not bool(obj.get("is_deleted", False)),
                        },
                    )
                    categories_by_id[obj.get("id")] = category
                    synced_categories += 1

            for obj in catalog.get('objects', []) or []:
                if obj.get('type') == 'ITEM':
                    item_data = obj.get('item_data') or {}
                    cat_id = item_data.get("category_id")
                    category = categories_by_id.get(cat_id) if cat_id else None
                    for variation in item_data.get('variations', []) or []:
                        var_data = variation.get('item_variation_data') or {}
                        price_amount = int((var_data.get('price_money') or {}).get('amount', 0) or 0)
                        variation_name = var_data.get("name") or ""
                        name = item_data.get("name") or "Item"
                        if variation_name and variation_name.lower() not in ("regular", "default"):
                            name = f"{name} ({variation_name})"

                        menu_item, _ = MenuItem.objects.update_or_create(
                            restaurant=self.restaurant,
                            external_provider="SQUARE",
                            external_id=variation.get('id'),
                            defaults={
                                'category': category,
                                'name': name,
                                'description': item_data.get('description', ''),
                                'price': price_amount / 100,
                                'is_active': not bool(obj.get('is_deleted', False)),
                                'external_metadata': {
                                    "square_item_id": obj.get("id"),
                                    "square_variation_id": variation.get("id"),
                                    "square_version": self._square_version(),
                                },
                            }
                        )
                        synced_items.append(menu_item.id)
            
            return {
                'success': True,
                'items_synced': len(synced_items),
                'categories_synced': synced_categories,
                'provider': 'Square'
            }
        except Exception as e:
            return {'success': False, 'error': str(e)}
    
    def sync_orders(self, start_date=None, end_date=None) -> List[Dict]:
        """Sync orders from Square"""
        location_id = self.location_id or self.merchant_id
        query = {'location_ids': [location_id]} if location_id else {}
        if start_date:
            query['start_at'] = start_date.isoformat() if hasattr(start_date, "isoformat") else str(start_date)
        if end_date:
            query['end_at'] = end_date.isoformat() if hasattr(end_date, "isoformat") else str(end_date)

        body = {'query': query} if query else {}
        response = self._request("POST", "/orders/search", json=body)
        return (response.json() or {}).get('orders', [])
    
    def create_order(self, order: Order) -> Dict:
        """Push order to Square"""
        location_id = self.location_id or self.merchant_id
        if not location_id:
            raise ValueError("Square location_id is not configured for this restaurant")

        order_data = {
            'idempotency_key': str(uuid.uuid4()),
            'order': {
                'location_id': location_id,
                'line_items': [
                    {
                        'quantity': str(item.quantity),
                        **(
                            {'catalog_object_id': str(getattr(item.menu_item, "external_id", "") or "")}
                            if (getattr(item.menu_item, "external_provider", None) == "SQUARE" and getattr(item.menu_item, "external_id", None))
                            else {'name': getattr(item.menu_item, "name", "Item")}
                        ),
                        'base_price_money': {
                            'amount': int(item.unit_price * 100),
                            'currency': getattr(self.restaurant, "currency", "USD")
                        }
                    }
                    for item in order.line_items.all()
                ]
            }
        }
        response = self._request("POST", "/orders", json=order_data)
        return response.json() or {}
    
    def process_payment(self, payment: Payment) -> Dict:
        """Process payment through Square"""
        # Payment capture requires a source_id from Square (nonce/card on file). That is not available in this flow.
        # Keep placeholder response and ensure callers treat it as unsupported unless a Square source_id is provided.
        return {'success': False, 'error': 'Square payment processing requires a Square source_id (not implemented)'}


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
                    external_provider="CLOVER",
                    external_id=item['id'],
                    defaults={
                        'name': item['name'],
                        'description': item.get('description', ''),
                        'price': item.get('price', 0) / 100,  # Clover uses cents
                        'is_active': not item.get('hidden', False)
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
                for item in order.line_items.all()
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

    @classmethod
    def process_payment(cls, payment: Payment) -> Dict:
        """Process a payment via the external POS provider"""
        integration = cls.get_integration(payment.restaurant)
        if not integration:
            return {'success': False, 'error': 'No POS integration configured'}

        try:
            result = integration.process_payment(payment)
            return {'success': True, 'result': result} if result.get('success') else result
        except Exception as e:
            return {'success': False, 'error': str(e)}
