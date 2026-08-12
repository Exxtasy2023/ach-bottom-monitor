import os
import time
import threading
import requests
from flask import Flask

# =========================
# SETTINGS
# =========================

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])

CHECK_INTERVAL = int(os.getenv("INTERVAL_SECONDS", "900"))

ACH_SYMBOL = "ACHUSDT"
BTC_SYMBOL = "BTCUSDT"

# Важные уровни ACH
LEVELS = [0.0030, 0.0035, 0.0040, 0.0055, 0.0070, 0.0100]

app = Flask(__name__)

last_update_id = 0
last_alert = None


# =========================
# BINANCE DATA
# =========================

def get_klines(symbol, interval="1d", limit=100):
    response = requests.get(
        "https://api.binance.com/api/v3/klines",
        params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit
        },
        timeout=15
    )

    response.raise_for_status()
    return response.json()


def get_price(symbol):
    response = requests.get(
        "https://api.binance.com/api/v3/ticker/price",
        params={"symbol": symbol},
        timeout=15
    )

    response.raise_for_status()

    return float(response.json()["price"])


# =========================
# INDICATORS
# =========================

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

    rs = average_gain / average_loss

    return 100 - (100 / (1 + rs))


def percentage_change(old, new):

    if old == 0:
        return 0

    return ((new - old) / old) * 100


# =========================
# MARKET ANALYSIS
# =========================

def analyze():

    ach = get_klines(ACH_SYMBOL, "1d", 100)
    btc = get_klines(BTC_SYMBOL, "1d", 100)

    ach_closes = [float(x[4]) for x in ach]
    ach_volumes = [float(x[5]) for x in ach]

    btc_closes = [float(x[4]) for x in btc]

    price = ach_closes[-1]
    btc_price = btc_closes[-1]

    rsi = calculate_rsi(ach_closes)

    # 24h
    ach_24h = percentage_change(
        ach_closes[-2],
        ach_closes[-1]
    )

    btc_24h = percentage_change(
        btc_closes[-2],
        btc_closes[-1]
    )

    # 7 days
    ach_7d = percentage_change(
        ach_closes[-8],
        ach_closes[-1]
    )

    btc_7d = percentage_change(
        btc_closes[-8],
        btc_closes[-1]
    )

    # Volume
    average_volume = sum(ach_volumes[-21:-1]) / 20

    volume_ratio = (
        ach_volumes[-1] / average_volume
        if average_volume
        else 0
    )

    # Recent lows
    low_7d = min(ach_closes[-7:])
    low_30d = min(ach_closes[-30:])

    # Higher-low approximation
    previous_low = min(ach_closes[-10:-5])
    recent_low = min(ach_closes[-5:])

    higher_low = recent_low > previous_low

    # =========================
    # BOTTOM SCORE
    # =========================

    score = 0
    reasons = []

    # RSI
    if rsi is not None:

        if rsi < 25:
            score += 2
            reasons.append("RSI очень низкий")

        elif rsi < 30:
            score += 1
            reasons.append("RSI в зоне перепроданности")

    # Volume
    if volume_ratio >= 3:
        score += 2
        reasons.append("экстремальный объём")

    elif volume_ratio >= 2:
        score += 1
        reasons.append("повышенный объём")

    # Higher low
    if higher_low:
        score += 2
        reasons.append("формируется higher low")

    # Price near 30d low
    distance_from_low = (
        (price - low_30d) / low_30d * 100
        if low_30d
        else 0
    )

    if distance_from_low <= 5:
        score += 1
        reasons.append("цена близко к 30d минимуму")

    # BTC filter
    if btc_24h >= 0 and ach_24h < 0:
        score += 1
        reasons.append("ACH слабее BTC")

    elif btc_24h > 0 and ach_24h > 0:
        score += 1
        reasons.append("BTC и ACH растут вместе")

    # Recovery from recent low
    recovery = percentage_change(
        low_7d,
        price
    )

    if recovery >= 5:
        score += 1
        reasons.append("есть восстановление от минимума")

    # =========================
    # STATUS
    # =========================

    if score <= 2:
        status = "🟥 Дно пока не подтверждается"

    elif score <= 4:
        status = "🟧 Возможна капитуляция"

    elif score <= 6:
        status = "🟨 Формируются признаки дна"

    elif score <= 8:
        status = "🟢 Сильные признаки разворота"

    else:
        status = "🚀 Очень сильная техническая структура"

    return {
        "price": price,
        "btc_price": btc_price,
        "rsi": rsi,
        "ach_24h": ach_24h,
        "btc_24h": btc_24h,
        "ach_7d": ach_7d,
        "btc_7d": btc_7d,
        "volume_ratio": volume_ratio,
        "low_7d": low_7d,
        "low_30d": low_30d,
        "recovery": recovery,
        "higher_low": higher_low,
        "score": score,
        "status": status,
        "reasons": reasons
    }


# =========================
# TELEGRAM
# =========================

def send_telegram(text):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )


def format_status(data):

    reasons = data["reasons"]

    reason_text = (
        "\n".join("• " + x for x in reasons)
        if reasons
        else "• Значимых сигналов пока нет"
    )

    return (
        "📊 ACH BOTTOM MONITOR\n\n"

        f"💰 ACH: ${data['price']:.6f}\n"
        f"₿ BTC: ${data['btc_price']:,.0f}\n\n"

        f"24h ACH: {data['ach_24h']:+.2f}%\n"
        f"7d ACH: {data['ach_7d']:+.2f}%\n\n"

        f"RSI: {data['rsi']:.1f}\n"
        f"Объём: {data['volume_ratio']:.1f}× среднего\n\n"

        f"7d low: ${data['low_7d']:.6f}\n"
        f"30d low: ${data['low_30d']:.6f}\n"
        f"Отскок от 7d low: {data['recovery']:+.2f}%\n\n"

        f"🧭 BOTTOM SCORE: {data['score']}/10\n"
        f"{data['status']}\n\n"

        "Причины:\n"
        f"{reason_text}\n\n"

        "⚠️ Это технический мониторинг, "
        "а не финансовая рекомендация."
    )


# =========================
# TELEGRAM COMMANDS
# =========================

def telegram_commands():

    global last_update_id

    # Сообщаем о запуске
    send_telegram(
        "🟢 ACH Monitor запущен!\n\n"
        "Команды:\n"
        "/status — текущий анализ ACH\n"
        "/help — список команд"
    )

    while True:

        try:

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_TOKEN}/getUpdates"
            )

            response = requests.get(
                url,
                params={
                    "offset": last_update_id + 1,
                    "timeout": 30
                },
                timeout=40
            )

            updates = response.json().get("result", [])

            for update in updates:

                last_update_id = update["update_id"]

                message = update.get("message", {})
                text = message.get("text", "").strip()

                chat_id = str(
                    message.get("chat", {}).get("id", "")
                )

                # Отвечаем только владельцу
                if chat_id != CHAT_ID:
                    continue

                if text == "/start":

                    send_telegram(
                        "🟢 ACH Monitor работает.\n\n"
                        "Напиши /status для текущего анализа."
                    )

                elif text == "/status":

                    data = analyze()

                    send_telegram(
                        format_status(data)
                    )

                elif text == "/help":

                    send_telegram(
                        "🤖 ACH Monitor\n\n"
                        "/status — анализ ACH\n"
                        "/help — помощь"
                    )

        except Exception as error:

            print("Telegram error:", error)

            time.sleep(5)


# =========================
# AUTOMATIC MONITOR
# =========================

def automatic_monitor():

    global last_alert

    while True:

        try:

            data = analyze()

            # Уведомляем только если score существенно изменился
            current_state = (
                data["score"],
                round(data["price"], 6)
            )

            if (
                data["score"] >= 6
                and current_state != last_alert
            ):

                send_telegram(
                    "🚨 ACH SIGNAL\n\n"
                    + format_status(data)
                )

                last_alert = current_state

            elif data["score"] < 6:

                last_alert = None

        except Exception as error:

            print("Monitor error:", error)

        time.sleep(CHECK_INTERVAL)


# =========================
# RENDER HEALTH CHECK
# =========================

@app.get("/")
def home():

    return "ACH Bottom Monitor is running", 200


@app.get("/test")
def test():

    send_telegram(
        "🟢 ACH Monitor: Telegram connection works!"
    )

    return "Test message sent", 200


# =========================
# START
# =========================

if __name__ == "__main__":

    threading.Thread(
        target=telegram_commands,
        daemon=True
    ).start()

    threading.Thread(
        target=automatic_monitor,
        daemon=True
    ).start()

    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port
    )
