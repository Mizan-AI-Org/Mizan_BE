from django.core.management.base import BaseCommand
from accounts.models import CustomUser, Restaurant
from django.contrib.auth import get_user_model

class Command(BaseCommand):
    help = 'Creates initial data for the application'
    
    def handle(self, *args, **options):
        # Create a sample restaurant
        restaurant, created = Restaurant.objects.get_or_create(
            name="Demo Restaurant",
            defaults={
                'address': '123 Main Street, City, State',
                'phone': '+1234567890',
                'email': 'demo@restaurant.com'
            }
        )
        
        if created:
            self.stdout.write(
                self.style.SUCCESS('Successfully created demo restaurant')
            )
        
        # Check if super admin exists
        User = get_user_model()
        if not User.objects.filter(role='SUPER_ADMIN').exists():
            User.objects.create_superuser(
                email='owner@restaurant.com',
                password='admin123',
                first_name='Restaurant',
                last_name='Owner',
                role='SUPER_ADMIN',
                restaurant=restaurant,
                phone='+1234567890',
                is_verified=True
            )
            self.stdout.write(
                self.style.SUCCESS('Successfully created super admin user: owner@restaurant.com / admin123')
            )