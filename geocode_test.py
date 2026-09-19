import requests

USER_AGENT = "bixi-shortcut/0.1 (pbizzarri2003@gmail.coms)"

def geocode_address(address):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": address, "format": "json", "limit": 1}
    headers = {"User-Agent": USER_AGENT}
    response = requests.get(url, params=params, headers=headers, timeout=10)
    response.raise_for_status()
    results = response.json()
    if not results:
        return None
    return float(results[0]["lat"]), float(results[0]["lon"])

if __name__ == "__main__":
    coords = geocode_address("1000 Rue Sainte-Catherine Ouest, Montreal")
    print(coords)