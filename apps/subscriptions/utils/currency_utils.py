"""
Currency formatting utilities for subscription pricing.

Provides currency formatting with proper symbols and decimal places.
"""

from decimal import Decimal, ROUND_HALF_UP

# Currency configuration: symbol and decimal places
CURRENCY_CONFIG = {
    "USD": {"symbol": "$", "decimals": 2},
    "INR": {"symbol": "₹", "decimals": 2},
    "EUR": {"symbol": "€", "decimals": 2},
    "GBP": {"symbol": "£", "decimals": 2},
    "JPY": {"symbol": "¥", "decimals": 0},
    "CAD": {"symbol": "C$", "decimals": 2},
    "AUD": {"symbol": "A$", "decimals": 2},
    "CHF": {"symbol": "CHF", "decimals": 2},
    "CNY": {"symbol": "¥", "decimals": 2},
    "SGD": {"symbol": "S$", "decimals": 2},
    "HKD": {"symbol": "HK$", "decimals": 2},
    "NZD": {"symbol": "NZ$", "decimals": 2},
    "KRW": {"symbol": "₩", "decimals": 0},
    "BRL": {"symbol": "R$", "decimals": 2},
    "MXN": {"symbol": "MX$", "decimals": 2},
    "ZAR": {"symbol": "R", "decimals": 2},
    "SEK": {"symbol": "kr", "decimals": 2},
    "NOK": {"symbol": "kr", "decimals": 2},
    "DKK": {"symbol": "kr", "decimals": 2},
    "PLN": {"symbol": "zł", "decimals": 2},
    "TRY": {"symbol": "₺", "decimals": 2},
    "AED": {"symbol": "د.إ", "decimals": 2},
    "SAR": {"symbol": "﷼", "decimals": 2},
    "THB": {"symbol": "฿", "decimals": 2},
    "IDR": {"symbol": "Rp", "decimals": 0},
    "PHP": {"symbol": "₱", "decimals": 2},
    "MYR": {"symbol": "RM", "decimals": 2},
    "VND": {"symbol": "₫", "decimals": 0},
    "RUB": {"symbol": "₽", "decimals": 2},
}


def format_currency(price_cents: int, currency: str) -> str:
    """
    Format price in cents to currency string with proper symbol.

    Args:
        price_cents: Price in cents (e.g., 999 for $9.99)
        currency: ISO 4217 currency code (e.g., "USD", "INR")

    Returns:
        Formatted currency string (e.g., "$9.99", "₹332.32")

    Raises:
        ValueError: If currency is not supported

    Example:
        >>> format_currency(999, "USD")
        "$9.99"
        >>> format_currency(33232, "INR")
        "₹332.32"
        >>> format_currency(1000, "JPY")
        "¥10"
    """
    if currency not in CURRENCY_CONFIG:
        raise ValueError(f"Unsupported currency: {currency}")

    config = CURRENCY_CONFIG[currency]
    symbol = config["symbol"]
    decimals = config["decimals"]

    # Convert cents to main currency unit
    # For JPY/KRW/VND/IDR (0 decimals), cents = main unit
    if decimals == 0:
        amount = price_cents
    else:
        amount = Decimal(price_cents) / Decimal(100)

    # Format with proper decimal places
    if decimals == 0:
        formatted_amount = str(int(amount))
    else:
        # Use Decimal for precise rounding
        quantize_str = "0." + "0" * decimals
        amount_decimal = Decimal(str(amount)).quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)
        formatted_amount = str(amount_decimal)

    return f"{symbol}{formatted_amount}"


def get_currency_symbol(currency: str) -> str:
    """
    Get the symbol for a currency code.

    Args:
        currency: ISO 4217 currency code

    Returns:
        Currency symbol

    Raises:
        ValueError: If currency is not supported
    """
    if currency not in CURRENCY_CONFIG:
        raise ValueError(f"Unsupported currency: {currency}")

    return CURRENCY_CONFIG[currency]["symbol"]
