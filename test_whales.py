import requests
import os
from dotenv import load_dotenv
import json

load_dotenv("config/.env")

API_KEY = os.getenv("POLYMARKET_API_KEY")
CLOB = "https://clob.polymarket.com"
GAMMA = "https://gamma-api.polymarket.com"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

# Obtener token de un mercado activo
markets = requests.get(f"{GAMMA}/markets", params={
    "limit": 1,
    "active": "true",
    "order": "volume24hr",
    "ascending": "false"
}).json()

market = markets[0]
question = market.get("question", "")[:50]
tokens = json.loads(market.get("clobTokenIds", "[]"))
token_id = tokens[0]

print(f"Mercado: {question}")
print(f"Token: {token_id[:30]}...")

# Buscar trades con auth
r = requests.get(f"{CLOB}/trades", 
    params={"token_id": token_id, "limit": 5},
    headers=headers,
    timeout=5
)
print(f"Status: {r.status_code}")
print(f"Response: {r.text[:500]}")