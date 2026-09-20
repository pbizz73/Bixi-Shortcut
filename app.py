from flask import Flask, request, jsonify
import requests
import math

app = Flask(__name__)
app.json.ensure_ascii = False  # emit real "é" etc. instead of "\u00e9" escapes
# On Flask versions older than 2.3, use this instead:
# app.config['JSON_AS_ASCII'] = False

USER_AGENT = "bixi-shortcut/0.1 (pbizzarri2003@gmail.coms)"
STATION_INFO_URL = "https://gbfs.velobixi.com/gbfs/2-2/en/station_information.json"
STATION_STATUS_URL = "https://gbfs.velobixi.com/gbfs/2-2/en/station_status.json"
MONTREAL_VIEWBOX = "-73.75,45.70,-73.40,45.40"


# ---------- Geocoding ----------

import time
import threading

_geocode_cache = {}
_geocode_cache_lock = threading.Lock()
_last_nominatim_call = 0.0
_nominatim_lock = threading.Lock()
CACHE_TTL_SECONDS = 120          # how long a geocoded address stays cached
MIN_SECONDS_BETWEEN_CALLS = 1.1  # a little over Nominatim's 1/sec limit, for safety


def _nominatim_get(url, params, headers):
    """Every outbound Nominatim request goes through here — this is what
    guarantees we never send requests faster than the policy allows, even
    across different addresses or during rapid testing."""
    global _last_nominatim_call
    with _nominatim_lock:
        wait = MIN_SECONDS_BETWEEN_CALLS - (time.monotonic() - _last_nominatim_call)
        if wait > 0:
            time.sleep(wait)
        response = requests.get(url, params=params, headers=headers, timeout=10)
        _last_nominatim_call = time.monotonic()
    return response


def geocode_address(address, limit=5):
    # Serve from cache if we've resolved this exact address recently — this
    # is what stops /geocode, /stations, and /maps-url from each separately
    # re-hitting Nominatim for the same address within one navigation.
    cache_key = address.strip().lower()
    with _geocode_cache_lock:
        cached = _geocode_cache.get(cache_key)
        if cached and (time.monotonic() - cached["time"]) < CACHE_TTL_SECONDS:
            return cached["candidates"]

    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": USER_AGENT}

    # Tier 1: structured search, city LOCKED to Montreal. This treats the
    # whole input as a street name and never lets Nominatim guess the city
    # from the text — which is what avoids collisions with same-named towns
    # elsewhere in Quebec (e.g. the town of Sainte-Catherine on the South
    # Shore vs. Rue Sainte-Catherine in downtown Montreal).
    params = {
        "street": address,
        "city": "Montreal",
        "state": "Quebec",
        "country": "Canada",
        "format": "json",
        "limit": limit,
    }
    response = _nominatim_get(url, params, headers)
    response.raise_for_status()
    results = response.json()

    # Tier 2: free-text search, biased (not forced) toward Montreal via a
    # bounding box. Catches inputs that aren't a clean street name, like a
    # landmark ("Olympic Stadium") that Tier 1's street-only search can't match.
    if not results:
        params = {
            "q": address,
            "format": "json",
            "limit": limit,
            "countrycodes": "ca",
            "viewbox": MONTREAL_VIEWBOX,
            "bounded": 1,
        }
        response = _nominatim_get(url, params, headers)
        response.raise_for_status()
        results = response.json()

    # Tier 3: fully unbounded free-text search — last resort, for addresses
    # that genuinely aren't in the Montreal area at all.
    if not results:
        params.pop("bounded", None)
        params.pop("viewbox", None)
        response = _nominatim_get(url, params, headers)
        response.raise_for_status()
        results = response.json()

    seen = set()
    candidates = []
    for r in results:
        lat = round(float(r["lat"]), 5)
        lon = round(float(r["lon"]), 5)
        key = (lat, lon)
        if key in seen:
            continue
        seen.add(key)
        candidates.append({"display_name": r["display_name"], "lat": lat, "lon": lon})

    with _geocode_cache_lock:
        _geocode_cache[cache_key] = {"candidates": candidates, "time": time.monotonic()}

    return candidates


def resolve_destination(address, chosen_address=None):
    """Re-geocode, and pick either the single result or the one matching
    chosen_address exactly. Returns (lat, lon) or None if it can't resolve."""
    candidates = geocode_address(address)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]["lat"], candidates[0]["lon"]
    if not chosen_address:
        return None  # ambiguous and no choice was given
    for c in candidates:
        if c["display_name"] == chosen_address:
            return c["lat"], c["lon"]
    return None  # the chosen text didn't match any current candidate


# ---------- BIXI stations ----------

def haversine_meters(lat1, lon1, lat2, lon2):
    R = 6371000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def get_stations():
    info = requests.get(STATION_INFO_URL, timeout=10).json()["data"]["stations"]
    status = requests.get(STATION_STATUS_URL, timeout=10).json()["data"]["stations"]
    status_by_id = {s["station_id"]: s for s in status}

    stations = []
    for st in info:
        s = status_by_id.get(st["station_id"])
        if not s:
            continue
        stations.append({
            "name": st["name"],
            "lat": st["lat"],
            "lon": st["lon"],
            "docks_free": s.get("num_docks_available", 0),
        })
    return stations


def nearest_open_stations(dest_lat, dest_lon, limit=3):
    stations = get_stations()
    candidates = [
        {**st, "distance_m": round(haversine_meters(dest_lat, dest_lon, st["lat"], st["lon"]))}
        for st in stations
        if st["docks_free"] > 0
    ]
    candidates.sort(key=lambda s: s["distance_m"])
    return candidates[:limit]


def station_label(station):
    """The exact text shown in the menu AND the text matched against later —
    generated by this one function so the two can never drift apart."""
    return f"{station['name']} — {station['distance_m']}m — {station['docks_free']} docks free"


# ---------- Endpoints ----------

@app.route("/geocode")
def geocode_endpoint():
    address = request.args.get("address")
    if not address:
        return jsonify({"error": "missing_address"}), 400

    candidates = geocode_address(address)
    if not candidates:
        return jsonify({"error": "address_not_found"}), 404

    return jsonify({"candidates": [c["display_name"] for c in candidates]})


@app.route("/stations")
def stations_endpoint():
    address = request.args.get("address")
    chosen_address = request.args.get("chosen_address")
    if not address:
        return jsonify({"error": "missing_address"}), 400

    dest = resolve_destination(address, chosen_address)
    if dest is None:
        return jsonify({"error": "address_match_failed"}), 409
    dest_lat, dest_lon = dest

    stations = nearest_open_stations(dest_lat, dest_lon)
    if not stations:
        return jsonify({"error": "no_docks_nearby"}), 404

    return jsonify({"stations": [station_label(s) for s in stations]})


@app.route("/maps-url")
def maps_url_endpoint():
    address = request.args.get("address")
    chosen_address = request.args.get("chosen_address")
    chosen_station = request.args.get("chosen_station")
    if not address or not chosen_station:
        return jsonify({"error": "missing_params"}), 400

    dest = resolve_destination(address, chosen_address)
    if dest is None:
        return jsonify({"error": "address_match_failed"}), 409
    dest_lat, dest_lon = dest

    stations = nearest_open_stations(dest_lat, dest_lon)
    match = next((s for s in stations if station_label(s) == chosen_station), None)
    if match is None:
        return jsonify({"error": "station_match_failed"}), 409

    url = (
        "https://www.google.com/maps/dir/?api=1"
        f"&destination={match['lat']},{match['lon']}"
        "&travelmode=bicycling&dir_action=navigate"
    )
    return jsonify({"maps_url": url})


if __name__ == "__main__":
    app.run(debug=True, port=5000)