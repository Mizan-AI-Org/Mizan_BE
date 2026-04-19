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
    Comprehensive location validation for clock-in.

    Evaluates ALL active BusinessLocation rows for the tenant (multi-site
    chains) and passes if the user is inside any one of them. Falls back to
    the legacy Restaurant.* columns when no BusinessLocation rows exist (e.g.
    immediately after deploy on a DB that hasn't been migrated yet).
    """
    match, distance, nearest = find_matching_location(restaurant, user_lat, user_lon)
    if match is not None:
        return True, "Location verified"

    # No site defined at all — keep the legacy permissive behaviour so we
    # don't brick tenants that haven't configured anything yet.
    if nearest is None and (not restaurant.latitude or not restaurant.longitude):
        return True, "Restaurant location not configured"

    site_label = nearest.name if nearest is not None else 'restaurant'
    return False, f"Outside geofence. {distance:.0f}m from {site_label}"


def find_matching_location(restaurant, user_lat, user_lon):
    """
    Evaluate the user's coordinates against every active BusinessLocation on
    the tenant. Returns a tuple (match, distance, nearest):

      - match:    the BusinessLocation the user is currently inside, or None
      - distance: distance in metres (to match if hit, else to nearest site)
      - nearest:  the nearest active BusinessLocation by distance, or None if
                  the tenant has no configured locations at all

    The caller typically only cares about `match` ("can this person clock in?")
    but the other two are handy for error messages and analytics.
    """
    try:
        user_lat_f = float(user_lat)
        user_lon_f = float(user_lon)
    except (TypeError, ValueError):
        return None, None, None

    # Local import to keep utils.py importable during app init (no model
    # registry required at module load).
    from .models import BusinessLocation

    locations = list(
        BusinessLocation.objects.filter(restaurant=restaurant, is_active=True)
    )

    # Legacy fallback: older tenants might not have a BusinessLocation row
    # yet (e.g. tests, or a restaurant created via an old code path). Treat
    # Restaurant.* as a single ad-hoc site.
    if not locations:
        if restaurant.latitude is None or restaurant.longitude is None:
            return None, None, None
        dist = calculate_distance(
            float(restaurant.latitude), float(restaurant.longitude),
            user_lat_f, user_lon_f,
        )
        radius = float(restaurant.radius) if restaurant.radius else 100
        radius = max(5.0, min(100.0, radius))

        # Build a transient proxy so callers can still read .name / .id.
        class _LegacySite:
            id = None
            name = 'Main'
            latitude = restaurant.latitude
            longitude = restaurant.longitude
            radius = radius
            geofence_enabled = bool(restaurant.geofence_enabled)
        nearest = _LegacySite()
        if dist <= radius and nearest.geofence_enabled:
            return nearest, dist, nearest
        return None, dist, nearest

    best_match = None
    best_match_dist = None
    nearest = None
    nearest_dist = None

    for loc in locations:
        if loc.latitude is None or loc.longitude is None:
            continue
        dist = calculate_distance(
            float(loc.latitude), float(loc.longitude),
            user_lat_f, user_lon_f,
        )
        if nearest is None or dist < nearest_dist:
            nearest, nearest_dist = loc, dist
        if not loc.geofence_enabled:
            continue
        radius = float(loc.radius) if loc.radius else 100
        radius = max(5.0, min(100.0, radius))
        if dist <= radius and (best_match_dist is None or dist < best_match_dist):
            best_match, best_match_dist = loc, dist

    if best_match is not None:
        return best_match, best_match_dist, best_match
    return None, nearest_dist, nearest
    

def send_whatsapp(phone, message, template_name, language_code="en_US"):
    token = settings.WHATSAPP_ACCESS_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    verision = settings.WHATSAPP_API_VERSION

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    if message is not None:
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
