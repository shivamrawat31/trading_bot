def round_to_tick(price, tick_size=0.05):
    """
    Rounds a given price to the nearest tick size.
    Indian markets trade in ticks of 0.05.
    """
    return round(round(price / tick_size) * tick_size, 2)
