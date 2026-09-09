import pandas as pd
import numpy as np
import logging
from config import WICK_BODY_RATIO, STRUCTURAL_BUFFER_ATR

logger = logging.getLogger(__name__)

class Strategy:
    def __init__(self):
        pass

    def determine_htf_trend(self, df_htf: pd.DataFrame) -> dict:
        default_ret = {"ORH": None, "ORL": None}
        
        if df_htf is None or df_htf.empty:
            logger.warning("No HTF data provided.")
            return default_ret
            
        try:
            last_date = df_htf['datetime'].dt.date.iloc[-1]
            
            mask = (
                (df_htf['datetime'].dt.date == last_date) & 
                (df_htf['datetime'].dt.time >= pd.to_datetime('09:15').time()) &
                (df_htf['datetime'].dt.time < pd.to_datetime('09:45').time())
            )
            morning_candles = df_htf[mask]
            
            if morning_candles.empty:
                logger.warning("No morning candles found between 09:15 and 09:45.")
                return default_ret
                
            orh_val = float(morning_candles['high'].max())
            orl_val = float(morning_candles['low'].min())
            
            return {"ORH": orh_val, "ORL": orl_val}
            
        except Exception as e:
            logger.error(f"Error calculating Opening Range: {e}")
            return default_ret

    def calculate_signal(self, df_ltf: pd.DataFrame, or_data: dict) -> dict:
        default_ret = {"signal": "NONE", "score": 0, "atr": 0, "rvol": 0, "price": 0, "sl_price": 0, "target_price": 0, "strategy_tier": 1}
        
        if not or_data or or_data.get("ORH") is None or or_data.get("ORL") is None:
            return default_ret
            
        if df_ltf is None or len(df_ltf) < 20:
            logger.warning("Not enough LTF data to calculate indicators.")
            return default_ret
            
        try:
            # ATR 14
            high_low = df_ltf['high'] - df_ltf['low']
            high_close = np.abs(df_ltf['high'] - df_ltf['close'].shift())
            low_close = np.abs(df_ltf['low'] - df_ltf['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df_ltf['ATR_14'] = true_range.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            
            # Volume SMA 20
            df_ltf['Vol_SMA_20'] = df_ltf['volume'].rolling(window=20).mean()
            
            # Step 1: Calculate VWAP
            df_ltf['date'] = df_ltf['datetime'].dt.date
            df_ltf['tp'] = (df_ltf['high'] + df_ltf['low'] + df_ltf['close']) / 3
            df_ltf['vol_price'] = df_ltf['tp'] * df_ltf['volume']
            df_ltf['VWAP'] = df_ltf.groupby('date')['vol_price'].cumsum() / df_ltf.groupby('date')['volume'].cumsum()
            
            # Candles
            prev_candle = df_ltf.iloc[-3]
            closed = df_ltf.iloc[-2]
            
            # Step 2: Calculate anatomy for closed candle
            real_body = abs(closed['open'] - closed['close'])
            lower_wick = min(closed['open'], closed['close']) - closed['low']
            upper_wick = closed['high'] - max(closed['open'], closed['close'])
            
            # Technicals on the CLOSED candle
            vol = closed['volume']
            vol_sma = closed['Vol_SMA_20']
            atr = closed['ATR_14']
            vwap = closed['VWAP']
            close = closed['close']
            
            latest_price = df_ltf.iloc[-1]['close']
            rvol = vol / vol_sma if vol_sma > 0 else 0
            
            orh = or_data["ORH"]
            orl = or_data["ORL"]
            
            # Volume Confirmation
            if rvol >= 1.2:
                # Bullish Sweep (BUY)
                required_low = orl
                pierced_below_orl = prev_candle['low'] <= required_low or closed['low'] <= required_low
                closed_above_orl = close > orl
                wick_rule_bullish = lower_wick >= real_body * WICK_BODY_RATIO
                
                if pierced_below_orl and closed_above_orl and wick_rule_bullish:
                    sweep_low = min(prev_candle['low'], closed['low'])
                    sl_price = sweep_low - (STRUCTURAL_BUFFER_ATR * atr)
                    target_price = orh - (STRUCTURAL_BUFFER_ATR * atr)
                    return {"signal": "BUY", "score": 3, "atr": atr, "rvol": rvol, "price": latest_price, "sl_price": sl_price, "target_price": target_price, "strategy_tier": 1}
                
                # Bearish Sweep (SELL)
                required_high = orh
                pierced_above_orh = prev_candle['high'] >= required_high or closed['high'] >= required_high
                closed_below_orh = close < orh
                wick_rule_bearish = upper_wick >= real_body * WICK_BODY_RATIO
                
                if pierced_above_orh and closed_below_orh and wick_rule_bearish:
                    sweep_high = max(prev_candle['high'], closed['high'])
                    sl_price = sweep_high + (STRUCTURAL_BUFFER_ATR * atr)
                    target_price = orl + (STRUCTURAL_BUFFER_ATR * atr)
                    return {"signal": "SELL", "score": 3, "atr": atr, "rvol": rvol, "price": latest_price, "sl_price": sl_price, "target_price": target_price, "strategy_tier": 1}
                    
            return default_ret
            
        except Exception as e:
            logger.error(f"Error calculating LTF signal: {e}")
            return default_ret

    def calculate_strategy_2(self, df_ltf: pd.DataFrame) -> dict:
        default_ret = {"signal": "NONE", "score": 0, "atr": 0, "rvol": 0, "price": 0, "sl_price": 0, "target_price": 0, "strategy_tier": 2}
        
        if df_ltf is None or len(df_ltf) < 20:
            logger.warning("Not enough LTF data to calculate strategy 2.")
            return default_ret
            
        try:
            # ATR 14
            high_low = df_ltf['high'] - df_ltf['low']
            high_close = np.abs(df_ltf['high'] - df_ltf['close'].shift())
            low_close = np.abs(df_ltf['low'] - df_ltf['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df_ltf['ATR_14'] = true_range.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            
            # Volume SMA 20
            df_ltf['Vol_SMA_20'] = df_ltf['volume'].rolling(window=20).mean()
            
            # VWAP
            df_ltf['date'] = df_ltf['datetime'].dt.date
            df_ltf['tp'] = (df_ltf['high'] + df_ltf['low'] + df_ltf['close']) / 3
            df_ltf['vol_price'] = df_ltf['tp'] * df_ltf['volume']
            df_ltf['VWAP'] = df_ltf.groupby('date')['vol_price'].cumsum() / df_ltf.groupby('date')['volume'].cumsum()
            
            # RSI 14
            delta = df_ltf['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss.replace(0, np.nan)
            df_ltf['RSI_14'] = 100 - (100 / (1 + rs))
            df_ltf['RSI_14'] = df_ltf['RSI_14'].fillna(100) # Handle 0 division
            
            # Pullback Detection (Last 3 closed candles)
            last_3 = df_ltf.iloc[-4:-1]
            pullback = ((last_3['low'] <= last_3['VWAP']) & (last_3['high'] >= last_3['VWAP'])).any()
            
            structural_low = float(last_3['low'].min())
            structural_high = float(last_3['high'].max())
            
            closed = df_ltf.iloc[-2]
            
            vol = float(closed['volume'])
            vol_sma = float(closed['Vol_SMA_20'])
            atr = float(closed['ATR_14'])
            vwap = float(closed['VWAP'])
            close = float(closed['close'])
            rsi = float(closed['RSI_14'])
            
            latest_price = float(df_ltf.iloc[-1]['close'])
            rvol = vol / vol_sma if vol_sma > 0 else 0
            
            if pullback and vol > vol_sma:
                # Bullish Setup (BUY)
                if close > vwap and 45 <= rsi <= 60:
                    sl_price = structural_low - (STRUCTURAL_BUFFER_ATR * atr)
                    sl_dist = abs(latest_price - sl_price)
                    target_price = latest_price + (1.5 * sl_dist)
                    return {"signal": "BUY", "score": 2, "atr": atr, "rvol": rvol, "price": latest_price, "sl_price": sl_price, "target_price": target_price, "strategy_tier": 2}
                    
                # Bearish Setup (SELL)
                if close < vwap and 40 <= rsi <= 55:
                    sl_price = structural_high + (STRUCTURAL_BUFFER_ATR * atr)
                    sl_dist = abs(sl_price - latest_price)
                    target_price = latest_price - (1.5 * sl_dist)
                    return {"signal": "SELL", "score": 2, "atr": atr, "rvol": rvol, "price": latest_price, "sl_price": sl_price, "target_price": target_price, "strategy_tier": 2}
                    
            return default_ret
            
        except Exception as e:
            logger.error(f"Error calculating strategy 2 signal: {e}")
            return default_ret

    def evaluate_index_bias(self, df_index: pd.DataFrame) -> str:
        if df_index is None or len(df_index) < 20:
            logger.warning("Not enough index data to calculate bias.")
            return "NEUTRAL"
            
        try:
            df_index['date'] = df_index['datetime'].dt.date
            df_index['tp'] = (df_index['high'] + df_index['low'] + df_index['close']) / 3
            df_index['vol_price'] = df_index['tp'] * df_index['volume']
            
            # Prevent division by zero if volume is 0
            vol_cumsum = df_index.groupby('date')['volume'].cumsum()
            df_index['VWAP'] = df_index.groupby('date')['vol_price'].cumsum() / vol_cumsum.replace(0, np.nan)
            
            closed = df_index.iloc[-2]
            close_price = closed['close']
            vwap_val = closed['VWAP']
            
            if pd.isna(vwap_val):
                return "NEUTRAL"
                
            if close_price > vwap_val:
                return "BULLISH"
            elif close_price < vwap_val:
                return "BEARISH"
            else:
                return "NEUTRAL"
        except Exception as e:
            logger.error(f"Error calculating index bias: {e}")
            return "NEUTRAL"
