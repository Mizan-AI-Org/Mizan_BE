from django.contrib.auth.models import AbstractUser
from django.db import models
import uuid

class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('supervisor', 'Shift Supervisor'),
        ('server', 'Server/Waiter'),
        ('chef', 'Chef/Kitchen Staff'),
        ('cleaner', 'Cleaner/Support Staff'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='server')
    pin_code = models.CharField(max_length=6, unique=True, null=True, blank=True)
    restaurant = models.ForeignKey('Restaurant', on_delete=models.CASCADE, related_name='staff', null=True, blank=True)
    phone = models.CharField(max_length=20, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def save(self, *args, **kwargs):
        if not self.pin_code:
            self.pin_code = self.generate_pin()
        super().save(*args, **kwargs)
    
    def generate_pin(self):
        import random
        return str(random.randint(100000, 999999))
    
    def __str__(self):
        return f"{self.username} - {self.role}"

class Restaurant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.TextField()
    phone = models.CharField(max_length=20)
    email = models.EmailField()
    
    # Geolocation fields for clocking in & Out
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    geo_fence_radius = models.IntegerField(default=100)  # meters
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.name
class StaffProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='profile')
    contract_end_date = models.DateField(null=True, blank=True)
    health_card_expiry = models.DateField(null=True, blank=True)
    hourly_rate = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    notes = models.TextField(blank=True)
    
    def __str__(self):
        return f"Profile - {self.user.username}"