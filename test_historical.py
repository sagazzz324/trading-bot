import requests
import json

params = {
    "limit": 5,
    "active": "false",
    "closed": "true",
    "order": "volume",
    "ascending": "false"
}

response = requests.get("https://gamma-api.polymarket.com/markets", params=params)
markets = response.json()

print(f"Total: {len(markets)}\n")

for m in markets:
    print(f"Pregunta: {m.get('question', '')[:60]}")
    print(f"  active: {m.get('active')}")
    print(f"  closed: {m.get('closed')}")
    print(f"  volume: {m.get('volume')}")
    print(f"  outcomes: {m.get('outcomes')}")
    print(f"  outcomePrices: {m.get('outcomePrices')}")
    print()