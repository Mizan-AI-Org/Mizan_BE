"""
Custom exceptions for Mizan AI
"""
from rest_framework.exceptions import APIException


class RestaurantNotFound(APIException):
    status_code = 404
    default_detail = 'Restaurant not found.'
    default_code = 'restaurant_not_found'


class NoRestaurantAssociated(APIException):
    status_code = 400
    default_detail = 'No restaurant associated with this user.'
    default_code = 'no_restaurant_associated'


class InvalidOrderStatus(APIException):
    status_code = 400
    default_detail = 'Invalid order status.'
    default_code = 'invalid_order_status'


class InsufficientInventory(APIException):
    status_code = 400
    default_detail = 'Insufficient inventory.'
    default_code = 'insufficient_inventory'


class PaymentFailed(APIException):
    status_code = 400
    default_detail = 'Payment processing failed.'
    default_code = 'payment_failed'