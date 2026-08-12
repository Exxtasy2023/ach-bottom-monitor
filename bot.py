import os
import time
import threading
import requests
from flask import Flask

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

SYMBOL = "ACHUSDT"
INTERVAL = int(os.getenv("INTERVAL_SECONDS", "900"))

app = Flask(__name__)


@app.get("/")
def health():
    return "ACH monitor is running", 200


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text},
        timeout=15
    )


def get_klines(limit=100):
    response = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={
            "symbol": SYMBOL,
            "interval": "1d",
            "limit": limit
        },
        timeout=15
    )

    response.raise_for_status()
    return response.json()


def calculate_rsi(closes, period=14):
    if len(closes) <= period:
        return None

    gains = []
    losses = []

    for old, new in zip(
        closes[-period - 1:-1],
        closes[-period:]
    ):
        change = new - old
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100

    relative_strength = average_gain / average_loss

    return 100 - (100 / (1 + relative_strength))


def monitor():
    last_signal = None

    while True:
        try:
            candles = get_klines()

            closes = [float(candle[4]) for candle in candles]
            volumes = [float(candle[5]) for candle in candles]

            price = closes[-1]
            rsi = calculate_rsi(closes)

            average_volume = sum(volumes[-21:-1]) / 20
            volume_ratio = volumes[-1] / average_volume

            signals = []

            if price <= 0.0030:
                signals.append(
                    "🚨 ACH достиг $0.0030 или ниже"
                )

            elif price <= 0.0035:
                signals.append(
                    "🟧 ACH вошёл в зону $0.0030–0.0035"
                )

            elif price >= 0.0055:
                signals.append(
                    "🟢 ACH выше $0.0055 — важный уровень силы"
                )

            if rsi is not None and rsi < 30:
                signals.append(
                    f"📉 RSI: {rsi:.1f} — перепроданность"
                )

            if volume_ratio >= 2:
                signals.append(
                    f"🔊 Объём: {volume_ratio:.1f}× среднего"
                )

            if (
                len(closes) >= 5
                and closes[-1] > min(closes[-4:-1])
            ):
                signals.append(
                    "🟨 Цена пытается сформировать higher low"
                )

            if signals:
                message = (
                    "ACH MONITOR\n\n"
                    f"Цена: ${price:.6f}\n"
                    f"RSI: {rsi:.1f}\n"
                    f"Объём/средний: {volume_ratio:.1f}×\n\n"
                    + "\n".join(signals)
                )

                signal_key = "|".join(signals)

                if signal_key != last_signal:
                    send_telegram(message)
                    last_signal = signal_key

            else:
                last_signal = None

        except Exception as error:
            print("Monitor error:", error)

        time.sleep(INTERVAL)


if __name__ == "__main__":
    thread = threading.Thread(
        target=monitor,
        daemon=True
    )

    thread.start()

    port = int(os.getenv("PORT", "10000"))

    app.run(
        host="0.0.0.0",
        port=port
    )
