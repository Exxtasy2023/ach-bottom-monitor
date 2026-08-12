import os
import time
import threading
import requests
from flask import Flask

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = str(os.environ["CHAT_ID"])

CHECK_INTERVAL = int(os.getenv("INTERVAL_SECONDS", "900"))

ACH_SYMBOL = "ACHUSDT"
BTC_SYMBOL = "BTCUSDT"

app = Flask(__name__)

telegram_thread_started = False
monitor_thread_started = False

last_update_id = 0
last_alert = None


# =========================
# TELEGRAM
# =========================

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text
        },
        timeout=15
    )

    print("Telegram response:", response.status_code)


def telegram_commands():
    global last_update_id

    print("Telegram command listener started")

    send_telegram(
        "🟢 ACH Monitor запущен!\n\n"
        "Команды:\n"
        "/status — текущий анализ ACH\n"
        "/help — список команд"
    )

    while True:

        try:

            response = requests.get(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
                params={
                    "offset": last_update_id + 1,
                    "timeout": 25
                },
                timeout=35
            )

            data = response.json()

            for update in data.get("result", []):

                last_update_id = update["update_id"]

                message = update.get("message", {})

                text = message.get("text", "").strip()

                chat_id = str(
                    message.get("chat", {}).get("id", "")
                )

                print(
                    f"Telegram message: {text} "
                    f"from chat {chat_id}"
                )

                if chat_id != CHAT_ID:
                    continue

                if text == "/start":

                    send_telegram(
                        "🟢 ACH Monitor работает.\n\n"
                        "Напиши /status для анализа ACH."
                    )

                elif text == "/status":

                    try:

                        data = analyze()

                        send_telegram(
                            format_status(data)
                        )

                    except Exception as error:

                        print("Status error:", error)

                        send_telegram(
                            "⚠️ Не удалось получить данные ACH.\n"
                            "Попробуй ещё раз через минуту."
                        )

                elif text == "/help":

                    send_telegram(
                        "🤖 ACH Bottom Monitor\n\n"
                        "/start — проверить бота\n"
                        "/status — анализ ACH\n"
                        "/help — список команд"
                    )

        except Exception as error:

            print("Telegram listener error:", error)

            time.sleep(5)


# =========================
# MARKET DATA
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
# ANALYSIS
# =========================

def analyze():

    ach = get_klines(
        ACH_SYMBOL,
        "1d",
        100
    )

    btc = get_klines(
        BTC_SYMBOL,
        "1d",
        100
    )

    ach_closes = [
        float(x[4])
        for x in ach
    ]

    ach_volumes = [
        float(x[5])
        for x in ach
    ]

    btc_closes = [
        float(x[4])
        for x in btc
    ]

    price = ach_closes[-1]

    btc_price = btc_closes[-1]

    rsi = calculate_rsi(
        ach_closes
    )

    ach_24h = percentage_change(
        ach_closes[-2],
        ach_closes[-1]
    )

    btc_24h = percentage_change(
        btc_closes[-2],
        btc_closes[-1]
    )

    ach_7d = percentage_change(
        ach_closes[-8],
        ach_closes[-1]
    )

    btc_7d = percentage_change(
        btc_closes[-8],
        btc_closes[-1]
    )

    average_volume = (
        sum(ach_volumes[-21:-1])
        / 20
    )

    volume_ratio = (
        ach_volumes[-1]
        / average_volume
        if average_volume
        else 0
    )

    low_7d = min(
        ach_closes[-7:]
    )

    low_30d = min(
        ach_closes[-30:]
    )

    previous_low = min(
        ach_closes[-10:-5]
    )

    recent_low = min(
        ach_closes[-5:]
    )

    higher_low = (
        recent_low > previous_low
    )

    score = 0

    reasons = []

    # RSI

    if rsi is not None:

        if rsi < 25:

            score += 2

            reasons.append(
                "RSI очень низкий"
            )

        elif rsi < 30:

            score += 1

            reasons.append(
                "RSI в зоне перепроданности"
            )

    # Volume

    if volume_ratio >= 3:

        score += 2

        reasons.append(
            "экстремальный объём"
        )

    elif volume_ratio >= 2:

        score += 1

        reasons.append(
            "повышенный объём"
        )

    # Higher low

    if higher_low:

        score += 2

        reasons.append(
            "формируется higher low"
        )

    # Near 30d low

    if low_30d > 0:

        distance = (
            (price - low_30d)
            / low_30d
            * 100
        )

        if distance <= 5:

            score += 1

            reasons.append(
                "цена близко к 30d минимуму"
            )

    # BTC filter

    if btc_24h >= 0 and ach_24h < 0:

        score += 1

        reasons.append(
            "ACH слабее BTC"
        )

    elif btc_24h > 0 and ach_24h > 0:

        score += 1

        reasons.append(
            "BTC и ACH растут вместе"
        )

    # Recovery

    recovery = percentage_change(
        low_7d,
        price
    )

    if recovery >= 5:

        score += 1

        reasons.append(
            "есть восстановление от 7d минимума"
        )

    # Status

    if score <= 2:

        status = (
            "🟥 Дно пока не подтверждается"
        )

    elif score <= 4:

        status = (
            "🟧 Возможна капитуляция"
        )

    elif score <= 6:

        status = (
            "🟨 Формируются признаки дна"
        )

    elif score <= 8:

        status = (
            "🟢 Сильные признаки разворота"
        )

    else:

        status = (
            "🚀 Очень сильная структура"
        )

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
# MESSAGE FORMAT
# =========================

def format_status(data):

    if data["reasons"]:

        reasons = "\n".join(
            "• " + x
            for x in data["reasons"]
        )

    else:

        reasons = (
            "• Значимых сигналов пока нет"
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

        f"Отскок: {data['recovery']:+.2f}%\n\n"

        f"🧭 BOTTOM SCORE: "
        f"{data['score']}/10\n"

        f"{data['status']}\n\n"

        "Причины:\n"
        f"{reasons}\n\n"

        "⚠️ Технический мониторинг, "
        "не финансовая рекомендация."
    )


# =========================
# AUTOMATIC MONITOR
# =========================

def automatic_monitor():

    global last_alert

    print(
        "Automatic monitor started"
    )

    while True:

        try:

            data = analyze()

            state = (
                data["score"],
                round(data["price"], 6)
            )

            if (
                data["score"] >= 6
                and state != last_alert
            ):

                send_telegram(
                    "🚨 ACH SIGNAL\n\n"
                    + format_status(data)
                )

                last_alert = state

            elif data["score"] < 6:

                last_alert = None

        except Exception as error:

            print(
                "Monitor error:",
                error
            )

        time.sleep(
            CHECK_INTERVAL
        )


# =========================
# START BACKGROUND THREADS
# =========================

def start_threads():

    global telegram_thread_started
    global monitor_thread_started

    if not telegram_thread_started:

        telegram_thread_started = True

        threading.Thread(
            target=telegram_commands,
            daemon=True
        ).start()

    if not monitor_thread_started:

        monitor_thread_started = True

        threading.Thread(
            target=automatic_monitor,
            daemon=True
        ).start()


# =========================
# FLASK
# =========================

@app.before_request
def ensure_threads():

    start_threads()


@app.get("/")
def home():

    return (
        "ACH Bottom Monitor is running",
        200
    )


@app.get("/test")
def test():
@app.get("/history-test")
def history_test():

    try:
        response = requests.get(
            "https://api.coingecko.com/api/v3/coins/alchemy-pay/market_chart",
            params={
                "vs_currency": "usd",
                "days": "max",
                "interval": "daily"
            },
            timeout=30
        )

        return (
            f"STATUS: {response.status_code}\n"
            f"LENGTH: {len(response.text)}\n"
            f"{response.text[:1000]}",
            200
        )

    except Exception as error:

        return f"ERROR: {error}", 500
    send_telegram(
        "🟢 ACH Monitor: "
        "Telegram connection works!"
    )

    return (
        "Test message sent",
        200
        )
