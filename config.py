import os
from dotenv import load_dotenv
load_dotenv()

# Fyers API Credentials
CLIENT_ID = os.getenv("FYERS_CLIENT_ID", "")
SECRET_KEY = os.getenv("FYERS_SECRET_KEY", "")
REDIRECT_URI = os.getenv("FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/index.html")

# Fyers Auth details for Selenium
FYERS_USER_ID = os.getenv("FYERS_USER_ID", "")
FYERS_PIN = os.getenv("FYERS_PIN", "")
FYERS_TOTP_SECRET = os.getenv("FYERS_TOTP_SECRET", "") # Used by pyotp

# Redis Configuration
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")

# Telegram Alerting
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Watchlist (Nifty 50 highly liquid stocks)
WATCHLIST = [
    "NSE:RELIANCE-EQ",
    "NSE:HDFCBANK-EQ",
    "NSE:ICICIBANK-EQ",
    "NSE:INFY-EQ",
    "NSE:HINDALCO-EQ",
    "NSE:SBIN-EQ",
    "NSE:BHARTIARTL-EQ",
    "NSE:ADANIPORTS-EQ",
    "NSE:LT-EQ",
    "NSE:KOTAKBANK-EQ"
]

# Trading Constants
HTF_TIMEFRAME = "15"
LTF_TIMEFRAME = "5"
MAX_DAILY_TRADES = 2
MAX_RISK_PER_TRADE = 150.0  # ₹150 max risk rule
DAILY_MAX_DRAWDOWN = -350.0 # -₹350 kill switch
DAILY_TARGET_PROFIT = 450.0 # ₹450 target profit rule
LEVERAGE = 5.0             # 5x MIS Leverage
STRUCTURAL_BUFFER_ATR = 0.1
MIN_RR_RATIO = 1.5
BUFFER_PERCENT = 0.001     # 0.1% buffer for limit orders
WICK_BODY_RATIO = 1.2
VWAP_MAGNET_BUFFER = 0.002
TRAIL_ACTIVATION_ATR = 1.5
TRAIL_DISTANCE_ATR = 1.5
INDEX_SYMBOL = "NSE:NIFTY50-INDEX"
USE_INDEX_FILTER = True

# Timing
MORNING_START = "09:45"
SQUARE_OFF_TIME = "15:00"
MARKET_CLOSE = "15:30"
GOLDEN_WINDOW_END = "14:30"
STALE_ORDER_MINUTES = 10   # 10 minutes (2 candles) pending order cancellation

# Indian Stock Market Holidays 2026 (YYYY-MM-DD format)
NSE_HOLIDAYS = [
    '2026-01-26', # Republic Day
    '2026-03-04', # Holi
    '2026-03-20', # Eid-Ul-Fitr (Ramadan Eid)
    '2026-03-27', # Shri Ram Navami
    '2026-03-31', # Shri Mahavir Jayanti
    '2026-04-03', # Good Friday
    '2026-04-14', # Dr. Baba Saheb Ambedkar Jayanti
    '2026-05-01', # Maharashtra Day
    '2026-05-26', # Bakri Id (Eid-Ul-Zuha)
    '2026-06-26', # Muharram
    '2026-09-14', # Ganesh Chaturthi
    '2026-10-02', # Mahatma Gandhi Jayanti
    '2026-10-20', # Dussehra
    '2026-11-09', # Diwali-Balipratipada
    '2026-11-24', # Prakash Gurpurb Sri Guru Nanak Dev
    '2026-12-25'  # Christmas
]

# Capital Slots initially
INITIAL_SLOTS = [2500, 2500]
