import argparse
import json
import os
import datetime

import streamlit as st
from streamlit_geolocation import streamlit_geolocation

st.set_page_config(page_title="GastroScout AI", page_icon="🍔", layout="wide")


def resolve_startup_mode() -> str:
    """Read mode from script args (after `--`) or GASTROSCOUT_MODE env var."""
    parser = argparse.ArgumentParser(description="GastroScout AI")
    parser.add_argument(
        "--gmaps",
        action="store_true",
        help="Start in Google Maps nearby mode.",
    )
    args, _ = parser.parse_known_args()
    if args.gmaps:
        return "gmaps"
    if os.getenv("GASTROSCOUT_MODE", "").strip().lower() == "gmaps":
        return "gmaps"
    return "hardcoded"


STARTUP_MODE = resolve_startup_mode()

from restaurant_menu_agent.scout import (
    scout_restaurant,
    scout_hardcoded_favorites,
    has_valid_menu,
    normalize_menu_result,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FAVORITES_JSON = os.path.join(BASE_DIR, "favorites_menu.json")
NEARBY_JSON = os.path.join(BASE_DIR, "nearby_menu.json")
DEFAULT_RADIUS_M = 3000
TARGET_RESTAURANT_COUNT = 3

MODE_LABELS = {
    "hardcoded": "Favorites (Fakin, Lobby, Cassandra)",
    "gmaps": "Nearby (Google Maps)",
}


def check_freshness(filepath):
    if not os.path.exists(filepath):
        return False
    file_date = datetime.date.fromtimestamp(os.path.getmtime(filepath))
    return file_date == datetime.date.today()


def render_restaurant_data(json_filepath):
    try:
        with open(json_filepath, "r", encoding="utf-8") as f:
            all_results = json.load(f)

        for restaurant in all_results:
            if not isinstance(restaurant, dict):
                continue

            rating = restaurant.get("google_rating")
            distance = restaurant.get("distance_m")
            meta_parts = []
            if rating:
                meta_parts.append(f"Google {rating}")
            if distance is not None:
                meta_parts.append(f"{distance} m away")
            title_suffix = f" | {' · '.join(meta_parts)}" if meta_parts else ""
            st.header(f"🏪 {restaurant.get('restaurant_name', 'Unknown')}{title_suffix}")
            st.subheader(f"Vibe: *{restaurant.get('general_vibe', '')}*")

            if not restaurant.get("is_daily_menu_available"):
                st.warning("No daily menu for today (restaurant may be closed).")
                st.divider()
                continue

            all_dishes = restaurant.get("specials", [])

            if not all_dishes:
                st.info("No specific dishes found.")
            else:
                for dish in all_dishes:
                    col1, col2 = st.columns([4, 1])
                    with col1:
                        st.markdown(f"### {dish.get('item_name')}")
                        st.write(dish.get("description", ""))
                        st.caption(f"Category: {dish.get('category')}")
                    with col2:
                        st.markdown(f"## {dish.get('price')}")
                st.divider()
    except Exception as e:
        st.error(f"Error reading data: {e}")


with st.sidebar:
    st.header("Mode")
    mode_label = st.radio(
        "Choose pipeline",
        options=list(MODE_LABELS.values()),
        index=0 if STARTUP_MODE == "hardcoded" else 1,
    )
    ACTIVE_MODE = "gmaps" if mode_label == MODE_LABELS["gmaps"] else "hardcoded"

if ACTIVE_MODE == "hardcoded":
    st.title("⭐️ GastroScout - My Favorites")
    st.write(f"Menu overview for: **{datetime.date.today().strftime('%d.%m.%Y')}**")

    col_refresh, _ = st.columns([1, 4])
    with col_refresh:
        if st.button("🔄 Scan Fakin, Lobby & Cassandra", use_container_width=True, type="primary"):
            with st.spinner("Scraping your favorite restaurants..."):
                results = scout_hardcoded_favorites()
                with open(FAVORITES_JSON, "w", encoding="utf-8") as f:
                    json.dump(results, f, indent=4, ensure_ascii=False)
            st.rerun()

    st.divider()

    if check_freshness(FAVORITES_JSON):
        render_restaurant_data(FAVORITES_JSON)
    else:
        st.info("⚠️ Click 'Scan' to load today's menus.")

else:
    from restaurant_menu_agent.maps_scout import discover_restaurants_with_menus, location_from_coords

    st.title("📍 GastroScout - Nearby Discovery")
    st.write(
        f"Restaurants within **{DEFAULT_RADIUS_M // 1000} km**, sorted by **Google rating** (highest first). "
        f"Shows the **full menu** for the top **{TARGET_RESTAURANT_COUNT}** with a scrapeable website."
    )

    search_radius = st.slider(
        "Search radius (meters):",
        min_value=500,
        max_value=5000,
        value=DEFAULT_RADIUS_M,
        step=500,
    )

    loc = streamlit_geolocation()

    if not loc or not loc.get("latitude"):
        st.info("Click the location button above and allow access when your browser asks.")
    else:
        lat, lng, label = location_from_coords(loc["latitude"], loc["longitude"])
        st.success(f"📍 Your location: **{label}** ({lat:.4f}, {lng:.4f})")

        if st.button("🚀 Scan Top Rated Nearby", use_container_width=True, type="primary"):
            with st.spinner(f"Finding restaurants within {search_radius}m, sorted by rating..."):
                try:
                    results = discover_restaurants_with_menus(
                        lat,
                        lng,
                        scout_fn=scout_restaurant,
                        has_menu_fn=has_valid_menu,
                        normalize_fn=normalize_menu_result,
                        radius=search_radius,
                        target=TARGET_RESTAURANT_COUNT,
                        menu_type="regular",
                    )

                    if not results:
                        st.warning(
                            "No nearby restaurants with a scrapeable menu were found. Try a larger radius."
                        )
                    else:
                        for entry in results:
                            distance = entry.get("distance_m")
                            entry["general_vibe"] = (
                                f"Google rating: {entry.get('google_rating')} "
                                f"({entry.get('google_reviews', 0)} reviews)"
                                f"{f' | {distance} m away' if distance is not None else ''} | "
                                f"{entry.get('general_vibe', '')}"
                            )

                        with open(NEARBY_JSON, "w", encoding="utf-8") as f:
                            json.dump(results, f, indent=4, ensure_ascii=False)
                        st.rerun()
                except Exception as e:
                    st.error(f"Error during nearby scan: {e}")

    st.divider()
    if check_freshness(NEARBY_JSON):
        render_restaurant_data(NEARBY_JSON)
