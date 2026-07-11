from math import radians, sin, cos, sqrt, atan2
import requests
from django.conf import settings


def calculate_distance(lat1, lon1, lat2, lon2):
    """
    Calculate the great-circle distance between two points
    on the Earth (specified in decimal degrees)
    Returns distance in meters
    """
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])

    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    r = 6371000  # Radius of earth in meters
    return c * r


def _normalize_polygon_ring(polygon):
    """
    Normalize a geofence polygon to a list of (lat, lon) tuples.

    Frontend stores ``Array<[lat, lng]>``. Also accept ``[{lat, lng}, …]``
    and ``[{latitude, longitude}, …]`` for older payloads.
    """
    if not polygon or not isinstance(polygon, (list, tuple)):
        return []
    ring = []
    for pt in polygon:
        try:
            if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                lat, lon = float(pt[0]), float(pt[1])
            elif isinstance(pt, dict):
                lat = float(pt.get("lat", pt.get("latitude")))
                lon = float(pt.get("lng", pt.get("lon", pt.get("longitude"))))
            else:
                continue
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                ring.append((lat, lon))
        except (TypeError, ValueError):
            continue
    return ring


def point_in_polygon(lat, lon, polygon):
    """
    Ray-casting point-in-polygon test.
    ``polygon`` is a ring of (lat, lon) points (same order as Leaflet/UI).
    """
    ring = _normalize_polygon_ring(polygon)
    if len(ring) < 3:
        return False
    # Close the ring if needed
    if ring[0] != ring[-1]:
        ring = list(ring) + [ring[0]]

    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        lat_i, lon_i = ring[i]
        lat_j, lon_j = ring[j]
        intersects = ((lon_i > lon) != (lon_j > lon)) and (
            lat < (lat_j - lat_i) * (lon - lon_i) / ((lon_j - lon_i) or 1e-15) + lat_i
        )
        if intersects:
            inside = not inside
        j = i
    return inside


def _clamp_radius_m(radius):
    try:
        r = float(radius) if radius is not None else 100.0
    except (TypeError, ValueError):
        r = 100.0
    return max(5.0, min(100.0, r))


def location_contains_point(loc, user_lat, user_lon):
    """
    True if ``(user_lat, user_lon)`` is inside this site's approved zone.

    Prefer a drawn polygon (≥3 vertices) when configured; otherwise use the
    circular radius around the site pin.
    """
    if loc is None:
        return False
    try:
        user_lat_f = float(user_lat)
        user_lon_f = float(user_lon)
    except (TypeError, ValueError):
        return False

    polygon = getattr(loc, "geofence_polygon", None) or []
    ring = _normalize_polygon_ring(polygon)
    if len(ring) >= 3:
        return point_in_polygon(user_lat_f, user_lon_f, ring)

    if getattr(loc, "latitude", None) is None or getattr(loc, "longitude", None) is None:
        return False
    dist = calculate_distance(
        float(loc.latitude),
        float(loc.longitude),
        user_lat_f,
        user_lon_f,
    )
    return dist <= _clamp_radius_m(getattr(loc, "radius", 100))


def is_within_geofence(restaurant_lat, restaurant_lon, user_lat, user_lon, radius_meters=100):
    """
    Check if user coordinates are within the restaurant's geofence
    """
    distance = calculate_distance(
        float(restaurant_lat),
        float(restaurant_lon),
        float(user_lat),
        float(user_lon),
    )
    return distance <= radius_meters


def restaurant_has_clockin_geofence(restaurant):
    """
    True when the tenant has at least one usable clock-in zone
    (any active BusinessLocation with coordinates, or legacy Restaurant lat/lon).
    """
    if restaurant is None:
        return False
    from .models import BusinessLocation

    for loc in BusinessLocation.objects.filter(restaurant=restaurant, is_active=True):
        if loc.latitude is not None and loc.longitude is not None:
            return True
        if len(_normalize_polygon_ring(loc.geofence_polygon or [])) >= 3:
            return True
    if restaurant.latitude is not None and restaurant.longitude is not None:
        return True
    if len(_normalize_polygon_ring(getattr(restaurant, "geofence_polygon", None) or [])) >= 3:
        return True
    return False


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

    site_label = nearest.name if nearest is not None else "restaurant"
    dist_label = f"{distance:.0f}m" if distance is not None else "unknown"
    return False, f"Outside geofence. {dist_label} from {site_label}"


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
        has_coords = restaurant.latitude is not None and restaurant.longitude is not None
        has_poly = len(_normalize_polygon_ring(getattr(restaurant, "geofence_polygon", None) or [])) >= 3
        if not has_coords and not has_poly:
            return None, None, None

        radius = _clamp_radius_m(restaurant.radius)

        class _LegacySite:
            id = None
            name = "Main"
            latitude = restaurant.latitude
            longitude = restaurant.longitude
            radius = radius
            geofence_enabled = bool(restaurant.geofence_enabled)
            geofence_polygon = getattr(restaurant, "geofence_polygon", None) or []
            address = getattr(restaurant, "address", "") or ""

        nearest = _LegacySite()
        dist = None
        if has_coords:
            dist = calculate_distance(
                float(restaurant.latitude),
                float(restaurant.longitude),
                user_lat_f,
                user_lon_f,
            )
        if not nearest.geofence_enabled:
            return nearest, dist if dist is not None else 0.0, nearest
        if location_contains_point(nearest, user_lat_f, user_lon_f):
            return nearest, dist if dist is not None else 0.0, nearest
        return None, dist, nearest

    best_match = None
    best_match_dist = None
    nearest = None
    nearest_dist = None

    for loc in locations:
        has_coords = loc.latitude is not None and loc.longitude is not None
        has_poly = len(_normalize_polygon_ring(loc.geofence_polygon or [])) >= 3
        if not has_coords and not has_poly:
            continue

        dist = None
        if has_coords:
            dist = calculate_distance(
                float(loc.latitude),
                float(loc.longitude),
                user_lat_f,
                user_lon_f,
            )
            if nearest is None or (dist is not None and dist < nearest_dist):
                nearest, nearest_dist = loc, dist
        elif nearest is None:
            # Polygon-only site — keep as a candidate nearest for messaging.
            nearest, nearest_dist = loc, None

        if not loc.geofence_enabled:
            # Geofence enforcement off — allow clock-in; prefer closest by pin.
            score = dist if dist is not None else float("inf")
            if best_match_dist is None or score < best_match_dist:
                best_match, best_match_dist = loc, score if score != float("inf") else 0.0
            continue

        if location_contains_point(loc, user_lat_f, user_lon_f):
            score = dist if dist is not None else 0.0
            if best_match_dist is None or score < best_match_dist:
                best_match, best_match_dist = loc, score

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
        "Content-Type": "application/json",
    }
    if message is not None:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
                "components": [{"type": "body", "parameters": message}],
            },
        }
        response = requests.post(url, json=payload, headers=headers)
    else:
        payload = {
            "messaging_product": "whatsapp",
            "to": phone,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language_code},
            },
        }
        response = requests.post(url, json=payload, headers=headers)
    try:
        data = response.json()
    except Exception:
        data = {"error": "Invalid JSON response"}

    # Return both response and parsed JSON to avoid losing info
    return {"status_code": response.status_code, "data": data}
