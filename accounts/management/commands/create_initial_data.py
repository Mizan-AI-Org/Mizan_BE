from django.core.management.base import BaseCommand
from accounts.models import CustomUser, Restaurant
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Creates initial data for the application'
    
    def handle(self, *args, **options):
        # Create a sample restaurant
        restaurant, created = Restaurant.objects.get_or_create(
            name="Test Restaurant",
            defaults={
                'address': '456 Test Avenue, Test City, TS',
                'phone': '+1987654321',
                'email': 'test@restaurant.com'
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('Successfully created test restaurant')
            )
        
        # Check if super admin exists for Test Restaurant
        User = get_user_model()
        if not User.objects.filter(restaurant=restaurant, role='SUPER_ADMIN').exists():
            User.objects.create_superuser(
                email='admin@testrestaurant.com',
                password='admin123',
                first_name='Test',
                last_name='Admin',
                role='SUPER_ADMIN',
                restaurant=restaurant,
                phone='+1987654321',
                is_verified=True
            )
            self.stdout.write(
                self.style.SUCCESS('Successfully created super admin user for Test Restaurant: admin@testrestaurant.com / admin123')
            )
        
        # Create staff for Test Restaurant
        staff_members = [
            {'email': 'waiter@testrestaurant.com', 'password': 'staff123', 'first_name': 'Test', 'last_name': 'Waiter', 'role': 'WAITER'},
            {'email': 'cleaner@testrestaurant.com', 'password': 'staff123', 'first_name': 'Test', 'last_name': 'Cleaner', 'role': 'CLEANER'},
            {'email': 'chef@testrestaurant.com', 'password': 'staff123', 'first_name': 'Test', 'last_name': 'Chef', 'role': 'CHEF'},
        ]
        
        for staff_data in staff_members:
            if not User.objects.filter(email=staff_data['email']).exists():
                User.objects.create_user(
                    email=staff_data['email'],
                    password=staff_data['password'],
                    first_name=staff_data['first_name'],
                    last_name=staff_data['last_name'],
                    role=staff_data['role'],
                    restaurant=restaurant,
                    is_verified=True
                )
                self.stdout.write(
                    self.style.SUCCESS(f'Successfully created {staff_data['role'].lower()} user: {staff_data['email']} / staff123')
                )