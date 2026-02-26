"""
POS Integration Manager
Handles synchronization with external POS providers (Toast, Square, Clover)
"""

import requests
from django.conf import settings
from django.db import models
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


class CustomAPIIntegration(BasePOSIntegration):
    """Custom API Integration — connects to any restaurant's own POS/sales API.

    Expected external API shape (flexible — we try multiple key names):
      GET /menu  → { "items"|"menu_items": [{ id, name, price, description?, active? }] }
      GET /orders → { "orders"|"data": [{ id, total|total_amount, status?, type|order_type?,
                        created_at|order_time?, items|line_items?: [{ name|menu_item, quantity, price|unit_price }],
                        payment_method? }] }
    """

    def __init__(self, restaurant):
        super().__init__(restaurant)
        root = restaurant.get_pos_oauth() or {}
        cfg = root.get('custom') or {}
        self.base_url = (cfg.get('api_url') or '').rstrip('/')
        self.auth_key = cfg.get('api_key') or self.api_key or ''

    def _headers(self):
        h = {'Content-Type': 'application/json'}
        if self.auth_key:
            h['Authorization'] = f'Bearer {self.auth_key}'
        return h

    def _request(self, method, path, **kwargs):
        url = f"{self.base_url}{path}" if self.base_url else path
        kwargs.setdefault('timeout', 15)
        kwargs.setdefault('headers', self._headers())
        resp = requests.request(method, url, **kwargs)
        resp.raise_for_status()
        return resp

    def sync_menu_items(self) -> Dict:
        try:
            resp = self._request('GET', '/menu')
            data = resp.json() if resp.content else {}
            items = data.get('items') or data.get('menu_items') or data.get('menu') or []
            synced = []
            for item in items:
                ext_id = str(item.get('id', ''))
                name = item.get('name', 'Item')
                defaults = {
                    'description': item.get('description', ''),
                    'price': float(item.get('price', 0)),
                    'is_active': item.get('active', True),
                }
                mi = MenuItem.objects.filter(
                    restaurant=self.restaurant, external_provider='CUSTOM', external_id=ext_id
                ).first()
                if mi:
                    for k, v in defaults.items():
                        setattr(mi, k, v)
                    mi.name = name
                    mi.save()
                else:
                    mi = MenuItem.objects.filter(restaurant=self.restaurant, name__iexact=name).first()
                    if mi:
                        mi.external_provider = 'CUSTOM'
                        mi.external_id = ext_id
                        for k, v in defaults.items():
                            setattr(mi, k, v)
                        mi.save()
                    else:
                        mi = MenuItem.objects.create(
                            restaurant=self.restaurant,
                            name=name,
                            external_provider='CUSTOM',
                            external_id=ext_id,
                            **defaults,
                        )
                synced.append(mi.id)
            return {'success': True, 'items_synced': len(synced), 'provider': 'Custom API'}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    # ------------------------------------------------------------------
    # Helpers to map loosely-structured external order JSON → Mizan models
    # ------------------------------------------------------------------

    @staticmethod
    def _pick(obj: dict, *keys, default=None):
        """Return the first non-None value from a dict for any of the given keys."""
        for k in keys:
            v = obj.get(k)
            if v is not None:
                return v
        return default

    def _resolve_menu_item(self, item_name: str):
        """Find or auto-create a MenuItem for this restaurant so we can attach line items."""
        if not item_name:
            item_name = "Unknown Item"
        mi = MenuItem.objects.filter(restaurant=self.restaurant, name__iexact=item_name).first()
        if not mi:
            try:
                mi = MenuItem.objects.create(
                    restaurant=self.restaurant,
                    name=item_name,
                    price=0,
                    external_provider='CUSTOM',
                    external_id=f'auto-{uuid.uuid4().hex[:8]}',
                )
            except Exception:
                mi = MenuItem.objects.filter(restaurant=self.restaurant, name__iexact=item_name).first()
                if not mi:
                    mi = MenuItem.objects.create(
                        restaurant=self.restaurant,
                        name=f"{item_name} ({uuid.uuid4().hex[:4]})",
                        price=0,
                        external_provider='CUSTOM',
                        external_id=f'auto-{uuid.uuid4().hex[:8]}',
                    )
        return mi

    def _parse_order_time(self, raw):
        """Best-effort parse of an ISO-ish datetime string."""
        if not raw:
            return None
        from django.utils.dateparse import parse_datetime as _pd
        dt = _pd(str(raw))
        if dt and timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt

    def sync_orders(self, start_date=None, end_date=None) -> List[Dict]:
        """Fetch orders from the external Custom API and persist them into Mizan's Order model."""
        import logging
        logger = logging.getLogger(__name__)
        params: Dict = {}
        if start_date:
            params['start_date'] = start_date.isoformat() if hasattr(start_date, 'isoformat') else str(start_date)
        if end_date:
            params['end_date'] = end_date.isoformat() if hasattr(end_date, 'isoformat') else str(end_date)
        try:
            resp = self._request('GET', '/orders', params=params)
            data = resp.json() if resp.content else {}
        except Exception as exc:
            logger.warning("Custom API /orders fetch failed: %s", exc)
            return []

        raw_orders = data.get('orders') or data.get('data') or data.get('results') or []
        if not isinstance(raw_orders, list):
            raw_orders = []

        imported = []
        for raw in raw_orders:
            ext_id = str(self._pick(raw, 'id', 'order_id', 'external_id', default=uuid.uuid4().hex[:12]))

            existing = Order.objects.filter(restaurant=self.restaurant, order_number=f'CUST-{ext_id}').first()
            if existing:
                imported.append(raw)
                continue

            total = float(self._pick(raw, 'total', 'total_amount', 'amount', 'grand_total', default=0))
            subtotal = float(self._pick(raw, 'subtotal', 'sub_total', default=total))
            tax = float(self._pick(raw, 'tax', 'tax_amount', default=0))
            discount = float(self._pick(raw, 'discount', 'discount_amount', default=0))

            order_type_map = {'dine_in': 'DINE_IN', 'takeout': 'TAKEOUT', 'delivery': 'DELIVERY', 'catering': 'CATERING'}
            raw_type = str(self._pick(raw, 'type', 'order_type', default='DINE_IN')).lower().replace('-', '_').replace(' ', '_')
            order_type = order_type_map.get(raw_type, 'DINE_IN')

            status_map = {'completed': 'COMPLETED', 'served': 'SERVED', 'cancelled': 'CANCELLED', 'pending': 'PENDING', 'paid': 'COMPLETED'}
            raw_status = str(self._pick(raw, 'status', default='COMPLETED')).lower()
            order_status = status_map.get(raw_status, 'COMPLETED')

            order_time = self._parse_order_time(self._pick(raw, 'created_at', 'order_time', 'date', 'timestamp'))

            order = Order(
                restaurant=self.restaurant,
                order_number=f'CUST-{ext_id}',
                order_type=order_type,
                status=order_status,
                subtotal=subtotal,
                tax_amount=tax,
                discount_amount=discount,
                total_amount=total or (subtotal + tax - discount),
                customer_name=self._pick(raw, 'customer_name', 'customer', default=''),
                notes=f'Imported from Custom API (ext_id={ext_id})',
            )
            if order_time:
                order.order_time = order_time
            order.save()

            line_items = self._pick(raw, 'items', 'line_items', 'order_items', default=[])
            if isinstance(line_items, list):
                for li in line_items:
                    item_name = str(self._pick(li, 'name', 'menu_item', 'item_name', 'product', default='Item'))
                    qty = int(self._pick(li, 'quantity', 'qty', default=1))
                    price = float(self._pick(li, 'price', 'unit_price', default=0))
                    mi = self._resolve_menu_item(item_name)
                    if mi.price == 0 and price > 0:
                        mi.price = price
                        mi.save(update_fields=['price'])
                    OrderLineItem.objects.create(
                        order=order,
                        menu_item=mi,
                        quantity=qty,
                        unit_price=price,
                        total_price=round(price * qty, 2),
                    )

            # Create a Payment record so cash/card breakdowns work
            pay_method_map = {'cash': 'CASH', 'card': 'CARD', 'credit_card': 'CARD', 'debit': 'CARD', 'digital_wallet': 'DIGITAL_WALLET', 'mobile': 'DIGITAL_WALLET'}
            raw_pm = str(self._pick(raw, 'payment_method', 'pay_method', 'payment_type', default='CASH')).lower().replace('-', '_').replace(' ', '_')
            pay_method = pay_method_map.get(raw_pm, 'CASH')

            if order_status in ('COMPLETED', 'SERVED') and total > 0:
                tip = float(self._pick(raw, 'tip', 'tip_amount', default=0))
                Payment.objects.create(
                    restaurant=self.restaurant,
                    order=order,
                    payment_method=pay_method,
                    amount=total,
                    tip_amount=tip,
                    status='COMPLETED',
                )

            imported.append(raw)

        if imported:
            self.restaurant.pos_is_connected = True
            self.restaurant.save(update_fields=['pos_is_connected'])

        return imported

    def create_order(self, order: Order) -> Dict:
        return {'success': False, 'error': 'Order creation not supported for custom API'}

    def process_payment(self, payment: Payment) -> Dict:
        return {'success': False, 'error': 'Payment processing not supported for custom API'}


class IntegrationManager:
    """Main manager for handling POS integrations"""
    
    PROVIDERS = {
        'TOAST': ToastIntegration,
        'SQUARE': SquareIntegration,
        'CLOVER': CloverIntegration,
        'CUSTOM': CustomAPIIntegration,
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

    @classmethod
    def get_daily_sales_summary(cls, restaurant, date=None) -> Dict:
        """Aggregate daily sales from Mizan POS orders (tenant-isolated by restaurant FK)."""
        from django.db.models import Sum, Count, Avg
        from decimal import Decimal

        if not restaurant.pos_is_connected and restaurant.pos_provider != 'NONE':
            return {'success': True, 'connected': False, 'error': 'POS not connected. Connect your POS in Settings.'}

        target_date = date or timezone.now().date()
        day_start = timezone.make_aware(timezone.datetime.combine(target_date, timezone.datetime.min.time()))
        day_end = timezone.make_aware(timezone.datetime.combine(target_date, timezone.datetime.max.time()))

        orders = Order.objects.filter(
            restaurant=restaurant,
            order_time__range=(day_start, day_end),
            status__in=['COMPLETED', 'SERVED'],
        )

        agg = orders.aggregate(
            total_sales=Sum('total_amount'),
            order_count=Count('id'),
            avg_ticket=Avg('total_amount'),
            total_tax=Sum('tax_amount'),
            total_discount=Sum('discount_amount'),
        )

        payments = Payment.objects.filter(
            restaurant=restaurant,
            payment_time__range=(day_start, day_end),
            status='COMPLETED',
        )
        payment_agg = payments.aggregate(
            total_tips=Sum('tip_amount'),
            cash_total=Sum('amount', filter=models.Q(payment_method='CASH')),
            card_total=Sum('amount', filter=models.Q(payment_method='CARD')),
        )

        by_type = {}
        for ot in orders.values('order_type').annotate(cnt=Count('id'), total=Sum('total_amount')):
            by_type[ot['order_type']] = {'count': ot['cnt'], 'total': float(ot['total'] or 0)}

        return {
            'success': True,
            'connected': True,
            'date': target_date.isoformat(),
            'total_sales': float(agg['total_sales'] or 0),
            'order_count': agg['order_count'] or 0,
            'avg_ticket': float(agg['avg_ticket'] or 0),
            'total_tax': float(agg['total_tax'] or 0),
            'total_discount': float(agg['total_discount'] or 0),
            'tips': float(payment_agg['total_tips'] or 0),
            'cash_total': float(payment_agg['cash_total'] or 0),
            'card_total': float(payment_agg['card_total'] or 0),
            'by_order_type': by_type,
            'currency': restaurant.currency or 'MAD',
        }

    @classmethod
    def get_top_selling_items(cls, restaurant, days=7, limit=10) -> Dict:
        """Top-selling menu items over a period (tenant-isolated)."""
        from django.db.models import Sum, Count

        cutoff = timezone.now() - timedelta(days=days)
        items = (
            OrderLineItem.objects.filter(
                order__restaurant=restaurant,
                order__order_time__gte=cutoff,
                order__status__in=['COMPLETED', 'SERVED'],
            )
            .values('menu_item__name', 'menu_item__id')
            .annotate(
                quantity=Sum('quantity'),
                revenue=Sum('total_price'),
                order_count=Count('order', distinct=True),
            )
            .order_by('-quantity')[:limit]
        )

        return {
            'success': True,
            'days': days,
            'items': [
                {
                    'item_id': str(i['menu_item__id']),
                    'name': i['menu_item__name'],
                    'quantity': i['quantity'],
                    'revenue': float(i['revenue'] or 0),
                    'order_count': i['order_count'],
                }
                for i in items
            ],
        }

    @classmethod
    def get_sales_analysis(cls, restaurant, days=7) -> Dict:
        """Sales analysis with trends, comparisons, and actionable recommendations."""
        from django.db.models import Sum, Count, Avg

        now = timezone.now()
        period_start = now - timedelta(days=days)
        prev_start = period_start - timedelta(days=days)

        def _period_stats(start, end):
            orders = Order.objects.filter(
                restaurant=restaurant,
                order_time__range=(start, end),
                status__in=['COMPLETED', 'SERVED'],
            )
            agg = orders.aggregate(
                total=Sum('total_amount'), count=Count('id'), avg=Avg('total_amount'),
            )
            return {
                'total': float(agg['total'] or 0),
                'count': agg['count'] or 0,
                'avg_ticket': float(agg['avg'] or 0),
            }

        current = _period_stats(period_start, now)
        previous = _period_stats(prev_start, period_start)

        revenue_change = 0
        if previous['total'] > 0:
            revenue_change = round(((current['total'] - previous['total']) / previous['total']) * 100, 1)

        top = cls.get_top_selling_items(restaurant, days, 5)
        slow = (
            OrderLineItem.objects.filter(
                order__restaurant=restaurant,
                order__order_time__gte=period_start,
                order__status__in=['COMPLETED', 'SERVED'],
            )
            .values('menu_item__name')
            .annotate(qty=Sum('quantity'))
            .order_by('qty')[:5]
        )

        recommendations = []
        if revenue_change < -10:
            recommendations.append(f"Revenue dropped {abs(revenue_change)}% vs previous {days} days. Consider running a promotion or daily special.")
        if current['avg_ticket'] > 0 and current['avg_ticket'] < previous['avg_ticket'] * 0.9:
            recommendations.append("Average ticket size is declining. Train servers on upselling (appetizers, desserts, drinks).")
        if top.get('items'):
            best = top['items'][0]
            recommendations.append(f"Your top seller is {best['name']} ({best['quantity']} sold). Ensure you're always stocked and consider a combo deal around it.")
        slow_items = list(slow)
        if slow_items:
            names = ', '.join([s['menu_item__name'] for s in slow_items[:3]])
            recommendations.append(f"Slowest items: {names}. Consider refreshing, repricing, or rotating them off the menu.")
        if not recommendations:
            recommendations.append("Sales are healthy. Keep up the good work!")

        return {
            'success': True,
            'period_days': days,
            'current': current,
            'previous': previous,
            'revenue_change_pct': revenue_change,
            'top_items': top.get('items', []),
            'slow_items': [{'name': s['menu_item__name'], 'quantity': s['qty']} for s in slow_items],
            'recommendations': recommendations,
            'currency': restaurant.currency or 'MAD',
        }

    @classmethod
    def generate_prep_list(cls, restaurant, target_date=None) -> Dict:
        """Generate a daily prep list based on recent sales averages, recipes, and inventory.
        Uses same-day-of-week sales from last 4 weeks as the forecast base."""
        from django.db.models import Sum, Avg
        from menu.models import RecipeIngredient
        from inventory.models import InventoryItem

        target = target_date or (timezone.now().date() + timedelta(days=1))
        dow = target.weekday()

        lookback_dates = [target - timedelta(weeks=w) for w in range(1, 5)]

        item_avg = (
            OrderLineItem.objects.filter(
                order__restaurant=restaurant,
                order__order_time__date__in=lookback_dates,
                order__status__in=['COMPLETED', 'SERVED'],
            )
            .values('menu_item__id', 'menu_item__name')
            .annotate(avg_qty=Avg('quantity'), total_qty=Sum('quantity'))
            .order_by('-avg_qty')
        )

        prep_items = []
        ingredient_totals = {}

        for row in item_avg:
            forecast_qty = round(float(row['avg_qty'] or 0) * 1.1, 1)
            prep_items.append({
                'menu_item': row['menu_item__name'],
                'forecast_portions': forecast_qty,
            })

            recipe_ings = RecipeIngredient.objects.filter(
                recipe__menu_item__id=row['menu_item__id'],
            ).select_related('ingredient')

            for ri in recipe_ings:
                ing_name = ri.ingredient.name
                qty_needed = float(ri.quantity) * forecast_qty
                if ing_name in ingredient_totals:
                    ingredient_totals[ing_name]['needed'] += qty_needed
                else:
                    inv_item = InventoryItem.objects.filter(
                        restaurant=restaurant, name__iexact=ing_name, is_active=True
                    ).first()
                    ingredient_totals[ing_name] = {
                        'needed': qty_needed,
                        'unit': ri.ingredient.unit if hasattr(ri.ingredient, 'unit') else (inv_item.unit if inv_item else ''),
                        'in_stock': float(inv_item.current_stock) if inv_item else None,
                    }

        prep_list = []
        shortages = []
        for name, info in sorted(ingredient_totals.items(), key=lambda x: -x[1]['needed']):
            entry = {
                'ingredient': name,
                'needed': round(info['needed'], 2),
                'unit': info['unit'],
                'in_stock': info['in_stock'],
            }
            if info['in_stock'] is not None:
                entry['gap'] = round(info['needed'] - info['in_stock'], 2)
                if entry['gap'] > 0:
                    shortages.append(f"{name}: need {entry['needed']}{info['unit']}, have {info['in_stock']}{info['unit']} (short {entry['gap']}{info['unit']})")
            prep_list.append(entry)

        return {
            'success': True,
            'target_date': target.isoformat(),
            'day_of_week': ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][dow],
            'forecast_portions': prep_items[:20],
            'ingredient_prep_list': prep_list[:30],
            'shortages': shortages,
            'message_for_user': cls._build_prep_message(target, dow, prep_items, prep_list, shortages),
        }

    @staticmethod
    def _build_prep_message(target, dow, prep_items, prep_list, shortages) -> str:
        day_name = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'][dow]
        if not prep_items:
            return "No sales history found for this day of week. Prep list couldn't be generated."
        header = f"📋 Prep list for {target.isoformat()} ({day_name}), based on last 4 weeks' sales:\n"
        if prep_list:
            lines = [f"• {p['ingredient']}: {p['needed']} {p['unit']}" + (f" (⚠️ short {p['gap']}{p['unit']})" if p.get('gap', 0) > 0 else " ✓") for p in prep_list[:15]]
            footer = f"\n\n⚠️ {len(shortages)} ingredient(s) may need reordering." if shortages else "\n\n✅ All ingredients in stock."
            return header + '\n'.join(lines) + footer
        lines = [f"• {p['menu_item']}: ~{p['forecast_portions']} portions" for p in prep_items[:15]]
        return header + '\n'.join(lines) + "\n\nℹ️ No recipes configured yet — showing forecast portions only. Add recipes in your menu to get ingredient-level prep lists."
