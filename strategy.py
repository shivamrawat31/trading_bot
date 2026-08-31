import pandas as pd
import numpy as np
import logging
from config import RSI_BULLISH_THRESHOLD, RSI_BEARISH_THRESHOLD

logger = logging.getLogger(__name__)

class Strategy:
    def __init__(self):
        pass

    def determine_htf_trend(self, df_htf: pd.DataFrame) -> str:
        if df_htf is None or len(df_htf) < 20:
            logger.warning("Not enough HTF data to calculate indicators.")
            return "NEUTRAL"
            
        try:
            df_htf['EMA_20'] = df_htf['close'].ewm(span=20, adjust=False).mean()
            
            delta = df_htf['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss
            df_htf['RSI_14'] = 100 - (100 / (1 + rs))
            
            closed = df_htf.iloc[-2]
            if closed['close'] > closed['EMA_20'] and closed['RSI_14'] > 50:
                return "BULLISH"
            elif closed['close'] < closed['EMA_20'] and closed['RSI_14'] < 50:
                return "BEARISH"
            else:
                return "NEUTRAL"
        except Exception as e:
            logger.error(f"Error determining HTF trend: {e}")
            return "NEUTRAL"

    def calculate_signal(self, df_ltf: pd.DataFrame, htf_trend: str) -> dict:
        default_ret = {"signal": "NONE", "score": 0, "atr": 0, "rvol": 0, "price": 0}
        
        if htf_trend == "NEUTRAL":
            return default_ret
            
        if df_ltf is None or len(df_ltf) < 20:
            logger.warning("Not enough LTF data to calculate indicators.")
            return default_ret
            
        try:
            # 1. EMA 20
            df_ltf['EMA_20'] = df_ltf['close'].ewm(span=20, adjust=False).mean()
            
            # 2. RSI 14
            delta = df_ltf['close'].diff()
            gain = delta.where(delta > 0, 0.0)
            loss = -delta.where(delta < 0, 0.0)
            avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            rs = avg_gain / avg_loss
            df_ltf['RSI_14'] = 100 - (100 / (1 + rs))
            
            # 3. ATR 14
            high_low = df_ltf['high'] - df_ltf['low']
            high_close = np.abs(df_ltf['high'] - df_ltf['close'].shift())
            low_close = np.abs(df_ltf['low'] - df_ltf['close'].shift())
            ranges = pd.concat([high_low, high_close, low_close], axis=1)
            true_range = np.max(ranges, axis=1)
            df_ltf['ATR_14'] = true_range.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
            
            # 4. Volume SMA 20
            df_ltf['Vol_SMA_20'] = df_ltf['volume'].rolling(window=20).mean()
            
            # Evaluate technicals on the CLOSED candle
            closed = df_ltf.iloc[-2]
            close = closed['close']
            ema = closed['EMA_20']
            rsi = closed['RSI_14']
            vol = closed['volume']
            vol_sma = closed['Vol_SMA_20']
            atr = closed['ATR_14']
            
            # Use the active forming candle for your entry limit price
            latest_price = df_ltf.iloc[-1]['close']
            
            rvol = vol / vol_sma if vol_sma > 0 else 0
            score = 0
            
            if htf_trend == "BULLISH":
                if close > ema: score += 1
                if rsi > RSI_BULLISH_THRESHOLD: score += 1
                if vol > vol_sma: score += 1
                
                if score >= 2:
                    return {"signal": "BUY", "score": score, "atr": atr, "rvol": rvol, "price": latest_price}
                    
            elif htf_trend == "BEARISH":
                if close < ema: score += 1
                if rsi < RSI_BEARISH_THRESHOLD: score += 1
                if vol > vol_sma: score += 1
                
                if score >= 2:
                    return {"signal": "SELL", "score": score, "atr": atr, "rvol": rvol, "price": latest_price}
                    
            return default_ret
            
        except Exception as e:
            logger.error(f"Error calculating LTF signal: {e}")
            return default_ret
