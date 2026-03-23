"""
Country to Currency mapping for geo-based pricing.

Maps ISO 3166-1 alpha-2 country codes to ISO 4217 currency codes.
"""

# Country code to currency code mapping
COUNTRY_TO_CURRENCY = {
    # Major markets
    "IN": "INR",  # India
    "US": "USD",  # United States
    "GB": "GBP",  # United Kingdom
    "DE": "EUR",  # Germany
    "FR": "EUR",  # France
    "JP": "JPY",  # Japan

    # Additional EU countries (EUR)
    "AT": "EUR",  # Austria
    "BE": "EUR",  # Belgium
    "CY": "EUR",  # Cyprus
    "EE": "EUR",  # Estonia
    "FI": "EUR",  # Finland
    "GR": "EUR",  # Greece
    "IE": "EUR",  # Ireland
    "IT": "EUR",  # Italy
    "LV": "EUR",  # Latvia
    "LT": "EUR",  # Lithuania
    "LU": "EUR",  # Luxembourg
    "MT": "EUR",  # Malta
    "NL": "EUR",  # Netherlands
    "PT": "EUR",  # Portugal
    "SK": "EUR",  # Slovakia
    "SI": "EUR",  # Slovenia
    "ES": "EUR",  # Spain
    "AD": "EUR",  # Andorra
    "MC": "EUR",  # Monaco
    "SM": "EUR",  # San Marino
    "VA": "EUR",  # Vatican City

    # Other major currencies
    "CA": "CAD",  # Canada
    "AU": "AUD",  # Australia
    "CH": "CHF",  # Switzerland
    "CN": "CNY",  # China
    "SG": "SGD",  # Singapore
    "HK": "HKD",  # Hong Kong
    "NZ": "NZD",  # New Zealand
    "SE": "SEK",  # Sweden
    "NO": "NOK",  # Norway
    "DK": "DKK",  # Denmark
    "MX": "MXN",  # Mexico
    "BR": "BRL",  # Brazil
    "ZA": "ZAR",  # South Africa
    "KR": "KRW",  # South Korea
    "AE": "AED",  # UAE
    "SA": "SAR",  # Saudi Arabia
    "TH": "THB",  # Thailand
    "MY": "MYR",  # Malaysia
    "ID": "IDR",  # Indonesia
    "PH": "PHP",  # Philippines
    "VN": "VND",  # Vietnam
    "TW": "TWD",  # Taiwan
    "TR": "TRY",  # Turkey
    "RU": "RUB",  # Russia
    "PL": "PLN",  # Poland
    "CZ": "CZK",  # Czech Republic
    "HU": "HUF",  # Hungary
    "IL": "ILS",  # Israel
    "EG": "EGP",  # Egypt
    "NG": "NGN",  # Nigeria
    "KE": "KES",  # Kenya
    "PK": "PKR",  # Pakistan
    "BD": "BDT",  # Bangladesh
    "LK": "LKR",  # Sri Lanka
    "NP": "NPR",  # Nepal
}


def get_currency_for_country(country_code: str) -> str:
    """
    Get the primary currency for a given country code.

    Args:
        country_code: ISO 3166-1 alpha-2 country code (e.g., "US", "IN")

    Returns:
        ISO 4217 currency code (e.g., "USD", "INR")
        Defaults to "USD" if country not found.

    Example:
        >>> get_currency_for_country("IN")
        "INR"
        >>> get_currency_for_country("US")
        "USD"
        >>> get_currency_for_country("XX")  # Unknown
        "USD"
    """
    if not country_code:
        return "USD"

    normalized_code = country_code.upper().strip()
    return COUNTRY_TO_CURRENCY.get(normalized_code, "USD")
