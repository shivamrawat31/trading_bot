import time
import logging
from datetime import datetime, timedelta
import urllib.parse
from fyers_apiv3 import fyersModel
import requests
import base64
import pyotp
import pandas as pd
import alerts

from config import CLIENT_ID, SECRET_KEY, REDIRECT_URI, FYERS_USER_ID, FYERS_PIN, FYERS_TOTP_SECRET

logger = logging.getLogger(__name__)

def get_base64(text):
    return base64.b64encode(text.encode('ascii')).decode('ascii')

class FyersClient:
    def __init__(self):
        self.access_token = None
        self.fyers = None

    def authenticate(self):
        """Authenticates using headless requests and PyOTP to bypass UI."""
        logger.info("Starting Fyers Authentication via backend APIs...")
        
        session = fyersModel.SessionModel(
            client_id=CLIENT_ID,
            secret_key=SECRET_KEY,
            redirect_uri=REDIRECT_URI,
            response_type="code",
            grant_type="authorization_code"
        )
        
        try:
            # 1. Send Login OTP
            payload1 = {
                "fy_id": get_base64(FYERS_USER_ID),
                "app_id": "2"
            }
            res1 = requests.post("https://api-t2.fyers.in/vagator/v2/send_login_otp_v2", json=payload1).json()
            if res1.get("s") != "ok":
                raise Exception(f"Failed to send OTP: {res1}")
            request_key = res1["request_key"]
            
            # 2. Verify TOTP
            totp = pyotp.TOTP(FYERS_TOTP_SECRET).now()
            payload2 = {
                "request_key": request_key,
                "otp": totp
            }
            res2 = requests.post("https://api-t2.fyers.in/vagator/v2/verify_otp", json=payload2).json()
            if res2.get("s") != "ok":
                raise Exception(f"Failed to verify TOTP: {res2}")
            request_key = res2["request_key"]
            
            # 3. Verify PIN
            payload3 = {
                "request_key": request_key,
                "identity_type": "pin",
                "identifier": get_base64(FYERS_PIN)
            }
            res3 = requests.post("https://api-t2.fyers.in/vagator/v2/verify_pin_v2", json=payload3).json()
            if res3.get("s") != "ok":
                raise Exception(f"Failed to verify PIN: {res3}")
            token = res3["data"]["access_token"]
            
            # 4. Generate Auth Code
            client_parts = CLIENT_ID.strip().split('-')
            app_prefix = client_parts[0]
            app_type = client_parts[1] if len(client_parts) > 1 else "100"
            
            payload4 = {
                "fyers_id": FYERS_USER_ID,
                "app_id": app_prefix,
                "redirect_uri": REDIRECT_URI,
                "appType": app_type,
                "code_challenge": "",
                "state": "None",
                "scope": "",
                "nonce": "",
                "response_type": "code",
                "create_cookie": True
            }
            headers = {"Authorization": f"Bearer {token}"}
            res4 = requests.post("https://api-t1.fyers.in/api/v3/token", json=payload4, headers=headers).json()
            if res4.get("s") != "ok":
                raise Exception(f"Failed to generate authcode: {res4}")
            
            auth_url = res4["Url"]
            
            # Parse Auth Code
            parsed_url = urllib.parse.urlparse(auth_url)
            auth_code = urllib.parse.parse_qs(parsed_url.query)['auth_code'][0]
            
            # Generate Access Token
            session.set_token(auth_code)
            response = session.generate_token()
            
            self.access_token = response["access_token"]
            self.fyers = fyersModel.FyersModel(client_id=CLIENT_ID, is_async=False, token=self.access_token, log_path="")
            logger.info("Successfully authenticated with Fyers!")
            
        except Exception as e:
            logger.error(f"Fyers API authentication failed: {e}")
            alerts.send_alert(f"Fyers API authentication failed: {e}", "critical")
            raise

    def get_history(self, symbol: str, resolution: str = "5", days: int = 5) -> pd.DataFrame:
        """Fetches historical data and returns a DataFrame."""
        if not self.fyers:
            raise Exception("Not authenticated.")
            
        # Time calculations (epoch)
        
        end_time = int(time.time())
        start_time = int((datetime.now() - timedelta(days=days)).timestamp())
        
        data = {
            "symbol": symbol,
            "resolution": resolution,
            "date_format": "0",
            "range_from": str(start_time),
            "range_to": str(end_time),
            "cont_flag": "1"
        }
        
        try:
            response = self.fyers.history(data=data)
            if response.get("s") == "ok":
                df = pd.DataFrame(response["candles"], columns=["datetime", "open", "high", "low", "close", "volume"])
                df['datetime'] = pd.to_datetime(df['datetime'], unit='s', utc=True).dt.tz_convert('Asia/Kolkata')
                return df
            else:
                logger.error(f"Failed to fetch historical data for {symbol}: {response}")
                return None
        except Exception as e:
            logger.error(f"Exception fetching historical data for {symbol}: {e}")
            return None

    def place_bracket_order(self, symbol, qty, limit_price, stop_loss_abs, take_profit_abs, side):
        """Places a bracket limit order. Wraps in try/except for BO blocks/margin limits."""
        data = {
            "symbol": symbol,
            "qty": qty,
            "type": 1, # 1 => Limit Order
            "side": side, # 1 => Buy, -1 => Sell
            "productType": "INTRADAY", # Intraday order with SL/TP
            "limitPrice": limit_price,
            "stopPrice": 0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
            "stopLoss": stop_loss_abs,      # Absolute difference
            "takeProfit": take_profit_abs,  # Absolute difference
            "legType": 1                    # 1 => Absolute points
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
