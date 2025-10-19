from django.test import TestCase

# Create your tests here.
from rest_framework.test import APITestCase
from rest_framework import status
from django.urls import reverse
from accounts.models import CustomUser, Restaurant

class RestaurantOwnerSignupViewTest(APITestCase):
    def test_owner_signup(self):
        url = reverse('owner-signup')
        data = {
            'restaurant': {
                'name': 'Test Restaurant',
                'address': '123 Test St',
                'phone': '+1234567890',
                'email': 'restaurant@example.com'
            },
            'user': {
                'email': 'owner@example.com',
                'password': 'password123',
                'first_name': 'Owner',
                'last_name': 'User',
                'phone': '+1987654321'
            }
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(Restaurant.objects.filter(email='restaurant@example.com').exists())
        self.assertTrue(CustomUser.objects.filter(email='owner@example.com').exists())
        user = CustomUser.objects.get(email='owner@example.com')
        self.assertEqual(user.role, 'SUPER_ADMIN')
        self.assertTrue(user.is_verified)

    def test_owner_signup_missing_data(self):
        url = reverse('owner-signup')
        data = {
            'restaurant': {
                'name': 'Test Restaurant',
            },
            'user': {
                'email': 'owner2@example.com',
                'password': 'password123',
            }
        }
        response = self.client.post(url, data, format='json')
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_owner_signup_existing_email(self):
        url = reverse('owner-signup')
        data = {
            'restaurant': {
                'name': 'Test Restaurant',
                'address': '123 Test St',
                'phone': '+1234567890',
                'email': 'restaurant@example.com'
            },
            'user': {
                'email': 'owner@example.com',
                'password': 'password123',
                'first_name': 'Owner',
                'last_name': 'User',
                'phone': '+1987654321'
            }
        }
        self.client.post(url, data, format='json')  # First signup
        response = self.client.post(url, data, format='json')  # Second signup with same email
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('email', response.data)
