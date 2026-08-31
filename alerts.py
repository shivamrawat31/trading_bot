"""
To get your TELEGRAM_BOT_TOKEN:
1. Message @BotFather on Telegram.
2. Send /newbot and follow instructions to create a bot.
3. Copy the HTTP API token provided by BotFather.

To get your TELEGRAM_CHAT_ID:
1. Message your newly created bot to start a chat.
2. Visit https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates in your browser.
3. Look for the "chat" object and copy the "id" value.
"""

import logging
import requests
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

def send_alert(message: str, level: str = "info") -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.debug(f"Alert not sent (Telegram not configured): {message}")
        return

    emoji_map = {
        "info": "ℹ️",
        "warning": "⚠️",
        "critical": "🚨"
    }
    emoji = emoji_map.get(level.lower(), "ℹ️")
    formatted_message = f"{emoji} {message}"
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": formatted_message
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        response.raise_for_status()
    except Exception as e:
        logger.error(f"Failed to send Telegram alert: {e}")
