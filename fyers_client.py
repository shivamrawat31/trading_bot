import time
import logging
import urllib.parse
from fyers_apiv3 import fyersModel
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import pyotp
import pandas as pd

from config import CLIENT_ID, SECRET_KEY, REDIRECT_URI, FYERS_USER_ID, FYERS_PIN, FYERS_TOTP_SECRET

logger = logging.getLogger(__name__)

class FyersClient:
    def __init__(self):
        self.access_token = None
        self.fyers = None

    def authenticate(self):
        """Authenticates using Selenium headless browser and PyOTP."""
        logger.info("Starting Fyers Authentication via Selenium...")
        
        session = fyersModel.SessionModel(
            client_id=CLIENT_ID,
            secret_key=SECRET_KEY,
            redirect_uri=REDIRECT_URI,
            response_type="code",
            grant_type="authorization_code"
        )
        
        auth_url = session.generate_authcode()
        
        # Setup Selenium Headless
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        
        try:
            driver.get(auth_url)
            wait = WebDriverWait(driver, 30)
            
            # 1. Login with Client ID
            login_id_input = wait.until(EC.presence_of_element_located((By.ID, "login_id")))
            login_id_input.send_keys(FYERS_USER_ID)
            driver.find_element(By.ID, "nextbtn").click()
            
            # 2. Enter TOTP
            totp_input = wait.until(EC.presence_of_element_located((By.ID, "totp")))
            totp = pyotp.TOTP(FYERS_TOTP_SECRET).now()
            totp_input.send_keys(totp)
            driver.find_element(By.ID, "submit-totp").click()
            
            # 3. Enter PIN
            pin_input = wait.until(EC.presence_of_element_located((By.ID, "pin")))
            pin_input.send_keys(FYERS_PIN)
            driver.find_element(By.ID, "submit-pin").click()
            
            # Wait for redirect
            wait.until(EC.url_contains("auth_code="))
            current_url = driver.current_url
            
            # Parse Auth Code
            parsed_url = urllib.parse.urlparse(current_url)
            auth_code = urllib.parse.parse_qs(parsed_url.query)['auth_code'][0]
            
            # Generate Access Token
            session.set_token(auth_code)
            response = session.generate_token()
            
            self.access_token = response["access_token"]
            self.fyers = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=self.access_token, log_path="")
            logger.info("Successfully authenticated with Fyers!")
            
        except Exception as e:
            logger.error(f"Fyers authentication failed via Selenium: {e}")
            driver.save_screenshot('selenium_error.png')
            raise
        finally:
            driver.quit()

    def get_historical_data(self, symbol, resolution="5", days_back=5):
        """Fetches historical data and returns a DataFrame."""
        if not self.fyers:
            raise Exception("Not authenticated.")
            
        # Time calculations (epoch)
        import time
        from datetime import datetime, timedelta
        
        end_time = int(time.time())
        start_time = int((datetime.now() - timedelta(days=days_back)).timestamp())
        
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "0",
            "range_from": str(start_time),
            "range_to": str(end_time),
            "cont_flag": "1"
        }
        
        response = self.fyers.history(data=data)
        if response.get("s") == "ok":
            df = pd.DataFrame(response["candles"], columns=["datetime", "open", "high", "low", "close", "volume"])
            df['datetime'] = pd.to_datetime(df['datetime'], unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
            return df
        else:
            logger.error(f"Failed to fetch historical data for {symbol}: {response}")
            return None

    def place_bracket_order(self, symbol, qty, limit_price, stop_loss_abs, take_profit_abs):
        """Places a bracket limit order. Wraps in try/except for BO blocks/margin limits."""
        data = {
            "symbol": symbol,
            "qty": qty,
            "type": 1, # 1 => Limit Order
            "side": 1, # 1 => Buy
            "productType": "BO", # Bracket Order
            "limitPrice": limit_price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": stop_loss_abs,      # Absolute difference
            "takeProfit": take_profit_abs   # Absolute difference
        }
        
        try:
            response = self.fyers.place_order(data=data)
            if response.get("s") == "ok":
                logger.info(f"Bracket order placed for {symbol}: {response}")
                return response.get("id")
            else:
                logger.error(f"Bracket order rejected for {symbol}: {response}")
                return None
        except Exception as e:
            logger.error(f"Exception while placing bracket order for {symbol}: {e}")
            return None

    def cancel_order(self, order_id):
        """Cancels a pending order."""
        try:
            data = {"id": order_id}
            response = self.fyers.cancel_order(data=data)
            logger.info(f"Cancel order response for {order_id}: {response}")
            return response.get("s") == "ok"
        except Exception as e:
            logger.error(f"Failed to cancel order {order_id}: {e}")
            return False

    def get_order_book(self):
        """Fetches the current order book."""
        try:
            return self.fyers.orderbook()
        except Exception as e:
            logger.error(f"Failed to fetch order book: {e}")
            return None
            
    def get_positions(self):
        """Fetches current positions."""
        try:
            return self.fyers.positions()
        except Exception as e:
            logger.error(f"Failed to fetch positions: {e}")
            return None

    def exit_position(self, position_id):
        """Exits a specific position."""
        try:
            data = {"id": position_id}
            response = self.fyers.exit_positions(data=data)
            logger.info(f"Exit position response for {position_id}: {response}")
            return response.get("s") == "ok"
        except Exception as e:
            logger.error(f"Failed to exit position {position_id}: {e}")
            return False
