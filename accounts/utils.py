from math import radians, sin, cos, sqrt, atan2
import requests
from django.conf import settings

def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two points 
    on the Earth (specified in decimal degrees)
    Returns distance in meters
    """
    # Convert decimal degrees to radians
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    
    # Haversine formula
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * atan2(sqrt(a), sqrt(1-a))
    r = 6371000  # Radius of earth in meters
    return c * r

def is_within_geofence(restaurant_lat, restaurant_lon, user_lat, user_lon, radius_meters=100):
    """
    Check if user coordinates are within the restaurant's geofence
    """
    distance = calculate_distance(
        float(restaurant_lat), 
        float(restaurant_lon), 
        float(user_lat), 
        float(user_lon)
    )
    return distance <= radius_meters

def validate_clockin_location(restaurant, user_lat, user_lon):
    """
    Comprehensive location validation for clock-in
    """
    if not restaurant.latitude or not restaurant.longitude:
        # If restaurant location not set, allow clock-in (for development)
        return True, "Restaurant location not configured"
    
    is_within = is_within_geofence(
        restaurant.latitude,
        restaurant.longitude,
        user_lat,
        user_lon,
        float(restaurant.radius) if restaurant.radius else 100
    )
    
    if is_within:
        return True, "Location verified"
    else:
        distance = calculate_distance(
            restaurant.latitude,
            restaurant.longitude,
            user_lat,
            user_lon
        )
        return False, f"Outside geofence. {distance:.0f}m from restaurant"
    

def send_whatsapp(phone, message, template_name, language_code="en_US"):
    token = settings.WHATSAPP_ACCESS_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    verision = settings.WHATSAPP_API_VERSION

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    if message is None:
        payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": language_code},
                        "components": [
                            {
                                "type": "body",
                                "parameters": message
                            }
                        ]
                    }
            }
        response = requests.post(url, json=payload, headers=headers)
    else:
        payload = {
                    "messaging_product": "whatsapp",
                    "to": phone,
                    "type": "template",
                    "template": {
                        "name": template_name,
                        "language": {"code": language_code}
                    }
            }
        response = requests.post(url, json=payload, headers=headers)
    try:
        data = response.json()
    except Exception:
        data = {"error": "Invalid JSON response"}

    # Return both response and parsed JSON to avoid losing info
    return {"status_code": response.status_code, "data": data}
