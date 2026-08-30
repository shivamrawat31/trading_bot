import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class Strategy:
    def __init__(self):
        pass

    def evaluate(self, df):
        """
        Evaluates the 5-min timeframe strategy logic.
        Requires a pandas DataFrame with OHLCV data.
        Returns a dictionary with score, atr, rel_vol, and current price.
        """
        if len(df) < 20:
            logger.warning("Not enough data to calculate indicators (need at least 20 candles).")
            return None
            
        # Calculate indicators natively using pandas and numpy
        
        # 1. EMA 20
        df['EMA_20'] = df['close'].ewm(span=20, adjust=False).mean()
        
        # 2. RSI 14 (Wilder's Smoothing)
        delta = df['close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        
        avg_gain = gain.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        
        rs = avg_gain / avg_loss
        df['RSI_14'] = 100 - (100 / (1 + rs))
        
        # 3. ATR 14 (Wilder's Smoothing)
        high_low = df['high'] - df['low']
        high_close = np.abs(df['high'] - df['close'].shift())
        low_close = np.abs(df['low'] - df['close'].shift())
        tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        df['ATR_14'] = tr.ewm(alpha=1/14, min_periods=14, adjust=False).mean()
        
        # 4. Volume SMA 20
        df['VOL_SMA_20'] = df['volume'].rolling(window=20).mean()

        # Drop NaNs to safely access the last row
        df.dropna(inplace=True)
        if len(df) == 0:
            return None

        # Get latest candle (assuming dataframe is sorted oldest to newest)
        latest = df.iloc[-2]
        
        price = latest['close']
        ema = latest['EMA_20']
        rsi = latest['RSI_14']
        atr = latest['ATR_14']
        volume = latest['volume']
        vol_sma = latest['VOL_SMA_20']
        
        # Calculate Relative Volume for Tie-Breaker
        rel_vol = volume / vol_sma if vol_sma > 0 else 0

        # Scoring Logic
        score = 0
        if price > ema:
            score += 1
        if rsi > 60:
            score += 1
        if volume > vol_sma:
            score += 1
            
        return {
            "score": score,
            "atr": atr,
            "rel_vol": rel_vol,
            "price": price
        }
