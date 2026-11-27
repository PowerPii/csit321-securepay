"""Currencies a payment can be made in (v2 multi-currency, docs/v2/FX.md).

The merchant is always paid in SGD: the ledger, fees, reserve and payouts stay in SGD. A
payment in another currency locks the day's rate when it is created; capture and refunds use
that same rate.

`minor` is how many digits the smallest unit has (amounts are integers of that unit). We use
0 for IDR: rupiah have no cents in practice, and it keeps S$10,000 of IDR within range.
"""
SETTLEMENT = "SGD"

CURRENCIES: dict[str, dict] = {
    "SGD": {"minor": 2, "numeric": "702", "symbol": "S$", "name": "Singapore dollar"},
    "USD": {"minor": 2, "numeric": "840", "symbol": "US$", "name": "US dollar"},
    "EUR": {"minor": 2, "numeric": "978", "symbol": "€", "name": "Euro"},
    "MYR": {"minor": 2, "numeric": "458", "symbol": "RM", "name": "Malaysian ringgit"},
    "THB": {"minor": 2, "numeric": "764", "symbol": "฿", "name": "Thai baht"},
    "IDR": {"minor": 0, "numeric": "360", "symbol": "Rp", "name": "Indonesian rupiah"},
    "PHP": {"minor": 2, "numeric": "608", "symbol": "₱", "name": "Philippine peso"},
    "JPY": {"minor": 0, "numeric": "392", "symbol": "¥", "name": "Japanese yen"},
}
CODES = tuple(CURRENCIES)
FOREIGN = tuple(c for c in CODES if c != SETTLEMENT)


def minor_units(code: str) -> int:
    return CURRENCIES[code]["minor"]


def format_amount(amount: int, code: str) -> str:
    """`S$45.90`, `THB 1,250.00`, `JPY 5,000` for emails, receipts and error messages.

    Other currencies use their code, not a symbol: plain-text email and the PDF receipt's
    standard fonts have no glyph for some of them, and "$" alone would be ambiguous.
    """
    info = CURRENCIES[code]
    prefix = "S$" if code == SETTLEMENT else f"{code} "
    sign = "-" if amount < 0 else ""
    if info["minor"] == 0:
        return f"{sign}{prefix}{abs(amount):,}"
    whole, part = divmod(abs(amount), 10 ** info["minor"])
    return f"{sign}{prefix}{whole:,}.{part:0{info['minor']}d}"
