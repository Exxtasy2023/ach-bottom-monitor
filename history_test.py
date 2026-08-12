import requests

url = "https://api.coingecko.com/api/v3/coins/alchemy-pay/market_chart"

params = {
    "vs_currency": "usd",
    "days": "max",
    "interval": "daily"
}

response = requests.get(
    url,
    params=params,
    timeout=30
)

print("STATUS:", response.status_code)
print("LENGTH:", len(response.text))
print(response.text[:500])
