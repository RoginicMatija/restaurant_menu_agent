import math
import time
import geocoder
import googlemaps
from dotenv import load_dotenv
import os

load_dotenv()
gmaps = googlemaps.Client(key=os.getenv("GOOGLE_MAPS_API_KEY"))

DEFAULT_RADIUS_M = 3000
DEFAULT_TARGET_COUNT = 3


def _reverse_geocode_label(lat: float, lng: float) -> str:
    results = gmaps.reverse_geocode((lat, lng))
    if results:
        return results[0].get("formatted_address", f"{lat:.4f}, {lng:.4f}")
    return f"{lat:.4f}, {lng:.4f}"


def get_laptop_location() -> tuple[float, float, str]:
    """Detect laptop location from its public IP (for CLI use)."""
    g = geocoder.ip("me")
    if not g.latlng:
        raise ValueError("Could not determine laptop location from IP address.")

    lat, lng = g.latlng[0], g.latlng[1]
    label = _reverse_geocode_label(lat, lng)
    return lat, lng, label


def location_from_coords(lat: float, lng: float) -> tuple[float, float, str]:
    """Label coordinates from browser/device geolocation."""
    return lat, lng, _reverse_geocode_label(lat, lng)


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _fetch_nearby_restaurants(lat: float, lng: float, radius: int):
    all_results = []
    response = gmaps.places_nearby(location=(lat, lng), radius=radius, type="restaurant")
    all_results.extend(response.get("results", []))

    while response.get("next_page_token") and len(all_results) < 60:
        time.sleep(2)
        response = gmaps.places_nearby(page_token=response["next_page_token"])
        all_results.extend(response.get("results", []))

    return all_results


def get_nearby_restaurants_by_rating(lat: float, lng: float, radius: int = DEFAULT_RADIUS_M):
    """
    All restaurants within radius that have a Google rating and website,
    sorted by rating (highest first), then by review count.
    """
    results = _fetch_nearby_restaurants(lat, lng, radius)
    candidates = []

    for place in results:
        location = place.get("geometry", {}).get("location", {})
        place_lat = location.get("lat")
        place_lng = location.get("lng")
        rating = place.get("rating")
        if place_lat is None or place_lng is None or not rating:
            continue

        distance = _distance_m(lat, lng, place_lat, place_lng)
        if distance > radius:
            continue

        details = gmaps.place(
            place_id=place["place_id"],
            fields=["name", "website", "rating", "user_ratings_total", "geometry"],
        )
        result = details.get("result", {})
        website = result.get("website")
        if not website:
            continue

        geo = result.get("geometry", {}).get("location", location)
        candidates.append({
            "name": result.get("name"),
            "url": website,
            "rating": result.get("rating"),
            "user_ratings_total": result.get("user_ratings_total", 0),
            "distance_m": round(_distance_m(lat, lng, geo["lat"], geo["lng"])),
        })

    candidates.sort(
        key=lambda item: (item["rating"], item["user_ratings_total"]),
        reverse=True,
    )
    return candidates


def get_nearby_restaurants(lat: float, lng: float, radius: int = DEFAULT_RADIUS_M, pool_size: int | None = None):
    ranked = get_nearby_restaurants_by_rating(lat, lng, radius=radius)
    if pool_size is not None:
        return ranked[:pool_size]
    return ranked


def get_ranked_restaurants(lat: float, lng: float, radius: int = DEFAULT_RADIUS_M, pool_size: int | None = None):
    return get_nearby_restaurants(lat, lng, radius=radius, pool_size=pool_size)


def get_top_rated_restaurants(lat: float, lng: float, radius: int = DEFAULT_RADIUS_M, limit: int = DEFAULT_TARGET_COUNT):
    return get_nearby_restaurants_by_rating(lat, lng, radius=radius)[:limit]


def discover_restaurants_with_menus(
    lat: float,
    lng: float,
    scout_fn,
    has_menu_fn,
    normalize_fn,
    radius: int = DEFAULT_RADIUS_M,
    target: int = DEFAULT_TARGET_COUNT,
    menu_type: str = "regular",
):
    """
    Collect all restaurants in radius, sort by Google rating, then walk the list
    until `target` restaurants with scrapeable full menus are found.
    """
    candidates = get_nearby_restaurants_by_rating(lat, lng, radius=radius)
    if not candidates:
        return []

    print(f"Found {len(candidates)} rated restaurants within {radius}m.")

    results = []
    for place in candidates:
        if len(results) >= target:
            break

        print(
            f"Trying {place['name']} "
            f"(⭐ {place['rating']}, {place['distance_m']}m away)..."
        )
        raw = scout_fn(place["name"], place["url"], menu_type=menu_type)
        data = normalize_fn(raw, display_name=place["name"])
        if not has_menu_fn(data, menu_type=menu_type):
            if raw and "gablec" in raw.lower():
                print("   Daily/gablec menu only, trying next...")
            elif raw and "no prices" in raw.lower():
                print("   No prices on menu, trying next...")
            else:
                print("   No usable full menu on website, trying next...")
            continue

        data["google_rating"] = place["rating"]
        data["google_reviews"] = place.get("user_ratings_total", 0)
        data["distance_m"] = place["distance_m"]
        results.append(data)
        print(f"   Full menu found ({len(results)}/{target}).")

    return results
