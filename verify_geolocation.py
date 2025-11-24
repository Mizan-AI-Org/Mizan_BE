import os
import sys
import django
from decimal import Decimal
import time

# Setup Django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
sys.path.insert(0, os.getcwd())
django.setup()

from accounts.models import Restaurant, CustomUser, StaffProfile
from rest_framework.test import APIRequestFactory, force_authenticate
from accounts.views_extended import RestaurantSettingsViewSet, StaffLocationViewSet

def run_verification():
    print("=" * 80)
    print("GEOLOCATION FEATURE VERIFICATION")
    print("=" * 80)

    # 1. Setup Data
    print("\n[1] Setting up test data...")
    
    # Create/Get Restaurant
    restaurant, created = Restaurant.objects.get_or_create(
        email="geo_test@example.com",
        defaults={
            "name": "Geo Test Restaurant",
            "address": "123 Test St",
            "phone": "555-0100"
        }
    )
    print(f"   - Restaurant: {restaurant.name} (ID: {restaurant.id})")

    # Create/Get Admin User
    admin_user, created = CustomUser.objects.get_or_create(
        email="admin_geo@example.com",
        defaults={
            "first_name": "Admin",
            "last_name": "User",
            "role": "SUPER_ADMIN",
            "restaurant": restaurant,
            "is_active": True,
            "is_staff": True,
            "is_superuser": True
        }
    )
    if created:
        admin_user.set_password("password123")
        admin_user.save()
    print(f"   - Admin User: {admin_user.email}")

    # Create/Get Staff User
    staff_user, created = CustomUser.objects.get_or_create(
        email="staff_geo@example.com",
        defaults={
            "first_name": "Staff",
            "last_name": "User",
            "role": "WAITER",
            "restaurant": restaurant,
            "is_active": True
        }
    )
    if created:
        staff_user.set_password("password123")
        staff_user.save()
    
    # Ensure staff profile exists
    StaffProfile.objects.get_or_create(user=staff_user)
    print(f"   - Staff User: {staff_user.email}")

    # 2. Configure Geolocation (as Admin)
    print("\n[2] Configuring Geolocation Settings...")
    factory = APIRequestFactory()
    view = RestaurantSettingsViewSet.as_view({'post': 'geolocation'})
    
    # Set location to NYC (Times Square approx)
    center_lat = 40.7580
    center_lon = -73.9855
    radius = 100 # meters

    data = {
        "latitude": center_lat,
        "longitude": center_lon,
        "radius": radius,
        "geofence_enabled": True
    }
    
    request = factory.post('/api/settings/geolocation/', data, format='json')
    force_authenticate(request, user=admin_user)
    response = view(request)
    
    if response.status_code == 200:
        print(f"✅ Settings updated: Lat={center_lat}, Lon={center_lon}, Radius={radius}m")
    else:
        print(f"❌ Failed to update settings: {response.data}")
        return

    # 3. Test: Staff Inside Geofence
    print("\n[3] Testing Staff INSIDE Geofence...")
    view_loc = StaffLocationViewSet.as_view({'post': 'update_location'})
    
    # Very close to center
    inside_lat = 40.7580
    inside_lon = -73.9855 
    
    data = {
        "latitude": inside_lat,
        "longitude": inside_lon
    }
    
    request = factory.post('/api/location/update-location/', data, format='json')
    force_authenticate(request, user=staff_user)
    response = view_loc(request)
    
    if response.status_code == 200:
        result = response.data
        print(f"   - Input: Lat={inside_lat}, Lon={inside_lon}")
        print(f"   - Distance: {result.get('distance_meters', 0):.2f} meters")
        print(f"   - Within Geofence: {result.get('within_geofence')}")
        
        if result.get('within_geofence') is True:
            print("✅ PASS: Correctly identified as INSIDE")
        else:
            print("❌ FAIL: Incorrectly identified as OUTSIDE")
    else:
        print(f"❌ API Error: {response.data}")

    # 4. Test: Staff Outside Geofence
    print("\n[4] Testing Staff OUTSIDE Geofence...")
    
    # Far away (Central Park)
    outside_lat = 40.7829
    outside_lon = -73.9654
    
    data = {
        "latitude": outside_lat,
        "longitude": outside_lon
    }
    
    request = factory.post('/api/location/update-location/', data, format='json')
    force_authenticate(request, user=staff_user)
    response = view_loc(request)
    
    if response.status_code == 200:
        result = response.data
        print(f"   - Input: Lat={outside_lat}, Lon={outside_lon}")
        print(f"   - Distance: {result.get('distance_meters', 0):.2f} meters")
        print(f"   - Within Geofence: {result.get('within_geofence')}")
        
        if result.get('within_geofence') is False:
            print("✅ PASS: Correctly identified as OUTSIDE")
        else:
            print("❌ FAIL: Incorrectly identified as INSIDE")
    else:
        print(f"❌ API Error: {response.data}")

    print("\n" + "=" * 80)
    print("VERIFICATION COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    run_verification()
