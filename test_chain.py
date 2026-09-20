import requests

BASE = "http://127.0.0.1:5000"
address = "500 Rue Sainte-Catherine"

r = requests.get(f"{BASE}/geocode", params={"address": address})
r.raise_for_status()
candidates = r.json()["candidates"]
print("Candidates:")
for c in candidates:
    print(" -", c)

chosen_address = candidates[0]
print(f"\nUsing: {chosen_address}\n")

r = requests.get(f"{BASE}/stations", params={"address": address, "chosen_address": chosen_address})
r.raise_for_status()
data = r.json()
if "error" in data:
    print("Error from /stations:", data["error"])
else:
    stations = data["stations"]
    print("Stations:")
    for s in stations:
        print(" -", s)

    chosen_station = stations[0]
    print(f"\nUsing: {chosen_station}\n")

    r = requests.get(f"{BASE}/station-location", params={
        "address": address,
        "chosen_address": chosen_address,
        "chosen_station": chosen_station,
    })
    r.raise_for_status()
    print(r.json())