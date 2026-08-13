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

import pandas as pd

HISTORY_FILE = "alchemy-pay.xlsx"

def get_current_ach_price():

    api_key = os.environ["CMC_API_KEY"]

    response = requests.get(
        "https://pro-api.coinmarketcap.com/v2/cryptocurrency/quotes/latest",
        headers={
            "X-CMC_PRO_API_KEY": api_key
        },
        params={
            "symbol": "ACH",
            "convert": "USD"
        },
        timeout=15
    )

    response.raise_for_status()

    payload = response.json()

    ach_data = payload["data"]["ACH"]

    if isinstance(ach_data, list):
        ach_data = ach_data[0]

    price = ach_data[
        "quote"
    ]["USD"]["price"]

    return float(price)
    
def load_ach_history():

    df = pd.read_excel(
        HISTORY_FILE,
        engine="openpyxl"
    )

    required_columns = [
        "timeOpen",
        "priceOpen",
        "priceHigh",
        "priceLow",
        "priceClose",
        "volume"
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:
        raise RuntimeError(
            "Missing columns: "
            + ", ".join(missing)
        )

    df["timeOpen"] = pd.to_numeric(
        df["timeOpen"],
        errors="coerce"
    )

    df["priceClose"] = pd.to_numeric(
        df["priceClose"],
        errors="coerce"
    )

    df["volume"] = pd.to_numeric(
        df["volume"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "timeOpen",
            "priceClose",
            "volume"
        ]
    )

    df = df.sort_values(
        "timeOpen"
    ).reset_index(
        drop=True
    )

    return df


def get_ach_history():

    df = load_ach_history()

    if len(df) < 31:
        raise RuntimeError(
            "Not enough ACH historical data"
        )

    return df



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

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    average_gain = sum(gains) / period
    average_loss = sum(losses) / period

    if average_loss == 0:
        return 100

    rs = average_gain / average_loss

    return 100 - (
        100 / (1 + rs)
    )


def percentage_change(old, new):

    if old == 0:
        return 0

    return (
        (new - old)
        / old
        * 100
    )

def historical_analysis(df):

    closes = df["priceClose"].astype(float).tolist()
    volumes = df["volume"].astype(float).tolist()

    if len(closes) < 130:
        return {
            "matches": 0,
            "avg_7": None,
            "median_7": None,
            "avg_30": None,
            "median_30": None,
            "avg_90": None,
            "median_90": None,
            "positive_7": None,
            "positive_30": None,
            "positive_90": None,
            "max_gain": None
        }

    # -------------------------
    # CURRENT STRUCTURE
    # -------------------------

    current_rsi = calculate_rsi(closes)

    current_price = closes[-1]

    current_low_30 = min(
        closes[-30:]
    )

    current_distance = (
        (current_price - current_low_30)
        / current_low_30
        * 100
        if current_low_30 > 0
        else 0
    )

    current_avg_volume = (
        sum(volumes[-21:-1])
        / 20
        if len(volumes) >= 21
        else 0
    )

    current_volume_ratio = (
        volumes[-1] / current_avg_volume
        if current_avg_volume > 0
        else 0
    )

    current_change_7d = percentage_change(
        closes[-8],
        closes[-1]
    )

    # -------------------------
    # FIND HISTORICAL MATCHES
    # -------------------------

    matches = []

    last_match_index = -999

    for i in range(31, len(closes) - 90):

        # Не берём близкие исторические дни.
        # Минимум 14 дней между сигналами.
        if i - last_match_index < 14:
            continue

        historical_closes = closes[:i + 1]

        rsi = calculate_rsi(
            historical_closes
        )

        if rsi is None:
            continue

        price = closes[i]

        low_30 = min(
            closes[i - 29:i + 1]
        )

        if low_30 <= 0:
            continue

        distance_to_low = (
            (price - low_30)
            / low_30
            * 100
        )

        avg_volume = (
            sum(volumes[i - 20:i])
            / 20
        )

        if avg_volume <= 0:
            continue

        volume_ratio = (
            volumes[i]
            / avg_volume
        )

        change_7d = percentage_change(
            closes[i - 7],
            price
        )

        # -------------------------
        # STRICT SIMILARITY
        # -------------------------

        similarity = 0

        # RSI
        if abs(
            rsi - current_rsi
        ) <= 5:

            similarity += 1

        # Near 30d low
        if abs(
            distance_to_low
            - current_distance
        ) <= 3:

            similarity += 1

        # Volume
        if abs(
            volume_ratio
            - current_volume_ratio
        ) <= 0.5:

            similarity += 1

        # 7d momentum
        if abs(
            change_7d
            - current_change_7d
        ) <= 3:

            similarity += 1

        # Требуем совпадение
        # минимум по 3 из 4 признаков.
        if similarity < 3:
            continue

        # -------------------------
        # FUTURE RETURNS
        # -------------------------

        result_7 = percentage_change(
            price,
            closes[i + 7]
        )

        result_30 = percentage_change(
            price,
            closes[i + 30]
        )

        result_90 = percentage_change(
            price,
            closes[i + 90]
        )

        # Максимальный рост в следующие 90 дней
        future_prices = closes[
            i + 1:i + 91
        ]

        max_future_price = max(
            future_prices
        )

        max_gain = percentage_change(
            price,
            max_future_price
        )

        matches.append({
            "return_7": result_7,
            "return_30": result_30,
            "return_90": result_90,
            "max_gain": max_gain
        })

        last_match_index = i

    # -------------------------
    # NO MATCHES
    # -------------------------

    if not matches:

        return {
            "matches": 0,
            "avg_7": None,
            "median_7": None,
            "avg_30": None,
            "median_30": None,
            "avg_90": None,
            "median_90": None,
            "positive_7": None,
            "positive_30": None,
            "positive_90": None,
            "max_gain": None
        }

    # -------------------------
    # STATISTICS
    # -------------------------

    returns_7 = [
        x["return_7"]
        for x in matches
    ]

    returns_30 = [
        x["return_30"]
        for x in matches
    ]

    returns_90 = [
        x["return_90"]
        for x in matches
    ]

    max_gains = [
        x["max_gain"]
        for x in matches
    ]

    def median(values):

        values = sorted(values)

        n = len(values)

        middle = n // 2

        if n % 2 == 1:

            return values[middle]

        return (
            values[middle - 1]
            + values[middle]
        ) / 2

    return {
        "matches": len(matches),

        "avg_7": (
            sum(returns_7)
            / len(returns_7)
        ),

        "median_7": median(
            returns_7
        ),

        "avg_30": (
            sum(returns_30)
            / len(returns_30)
        ),

        "median_30": median(
            returns_30
        ),

        "avg_90": (
            sum(returns_90)
            / len(returns_90)
        ),

        "median_90": median(
            returns_90
        ),

        "positive_7": (
            sum(
                1
                for x in returns_7
                if x > 0
            )
            / len(returns_7)
            * 100
        ),

        "positive_30": (
            sum(
                1
                for x in returns_30
                if x > 0
            )
            / len(returns_30)
            * 100
        ),

        "positive_90": (
            sum(
                1
                for x in returns_90
                if x > 0
            )
            / len(returns_90)
            * 100
        ),

        "max_gain": (
            sum(max_gains)
            / len(max_gains)
        )
    }
# =========================
# ANALYSIS
# =========================

def analyze():

    df = get_ach_history()

    closes = [
        float(x)
        for x in df["priceClose"].tolist()
    ]

    volumes = [
        float(x)
        for x in df["volume"].tolist()
    ]

    if len(closes) < 31:
        raise RuntimeError(
            "Not enough historical ACH data"
        )

    price = closes[-1]

    btc_price = 0

    rsi = calculate_rsi(
        closes
    )

    ach_24h = percentage_change(
        closes[-2],
        closes[-1]
    )

    ach_7d = percentage_change(
        closes[-8],
        closes[-1]
    )

    average_volume = (
        sum(volumes[-21:-1])
        / 20
    )

    volume_ratio = (
        volumes[-1]
        / average_volume
        if average_volume
        else 0
    )

    low_7d = min(
        closes[-7:]
    )

    low_30d = min(
        closes[-30:]
    )

    previous_low = min(
        closes[-10:-5]
    )

    recent_low = min(
        closes[-5:]
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
    historical = historical_analysis(
        df
    )
    return {
        "price": price,
        "btc_price": btc_price,
        "rsi": rsi,
        "ach_24h": ach_24h,
        "btc_24h": 0,
        "ach_7d": ach_7d,
        "btc_7d": 0,
        "volume_ratio": volume_ratio,
        "low_7d": low_7d,
        "low_30d": low_30d,
        "recovery": recovery,
        "higher_low": higher_low,
        "score": score,
        "status": status,
        "reasons": reasons,
        "historical": historical,
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

    historical = data.get(
        "historical",
        {}
    )

    matches = historical.get(
        "matches",
        0
    )

    if matches > 0:

        historical_text = (
            "📚 ИСТОРИЧЕСКИЕ АНАЛОГИ\n\n"

            f"Похожих случаев: {matches}\n\n"

            f"7 дней:\n"
            f"• среднее: "
            f"{historical['avg_7']:+.1f}%\n"
            f"• медиана: "
            f"{historical['median_7']:+.1f}%\n"
            f"• рост: "
            f"{historical['positive_7']:.0f}% случаев\n\n"

            f"30 дней:\n"
            f"• среднее: "
            f"{historical['avg_30']:+.1f}%\n"
            f"• медиана: "
            f"{historical['median_30']:+.1f}%\n"
            f"• рост: "
            f"{historical['positive_30']:.0f}% случаев\n\n"

            f"90 дней:\n"
            f"• среднее: "
            f"{historical['avg_90']:+.1f}%\n"
            f"• медиана: "
            f"{historical['median_90']:+.1f}%\n"
            f"• рост: "
            f"{historical['positive_90']:.0f}% случаев\n\n"

            f"Макс. рост за 90d: "
            f"{historical['max_gain']:+.1f}%"
        )

    else:

        historical_text = (
            "📚 ИСТОРИЧЕСКИЕ АНАЛОГИ\n\n"
            "Похожих исторических случаев "
            "не найдено."
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

        f"{historical_text}\n\n"

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

    send_telegram(
        "🟢 ACH Monitor: "
        "Telegram connection works!"
    )

    return (
        "Test message sent",
        200
    )

