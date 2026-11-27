"""Rates, conversion to SGD and the FX fee (docs/v2/FX.md).

All money is integers of the currency's smallest unit. A conversion rounds half up to the
SGD cent. The FX fee (FX_FEE_BPS, 1% confirmed by Fifa 28 Nov 2026) applies only to
payments in a currency other than SGD.
"""
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings

from apps.fx.currencies import FOREIGN, SETTLEMENT, minor_units
from apps.fx.models import RateSheet

REFERENCE = Path(__file__).with_name("reference_rates.json")


class RateError(ValueError):
    pass


def clean_rates(raw: dict) -> dict[str, str]:
    """Every foreign currency, a positive decimal with at most 10 places, nothing else."""
    rates = {k: v for k, v in raw.items() if not k.startswith("_")}
    unknown = set(rates) - set(FOREIGN)
    if unknown:
        raise RateError(f"Unknown currencies: {', '.join(sorted(unknown))}")
    missing = set(FOREIGN) - set(rates)
    if missing:
        raise RateError(f"Missing rates for: {', '.join(sorted(missing))}")
    clean = {}
    for code in FOREIGN:
        try:
            value = Decimal(str(rates[code]))
        except InvalidOperation:
            raise RateError(f"{code}: not a number") from None
        if not value.is_finite() or value <= 0 or value.as_tuple().exponent < -10:
            raise RateError(f"{code}: must be positive with at most 10 decimal places")
        clean[code] = format(value, "f")
    return clean


def reference_rates() -> dict[str, str]:
    return clean_rates(json.loads(REFERENCE.read_text()))


def publish(rates: dict, *, source: str = "", published_by: str = "") -> RateSheet:
    return RateSheet.objects.create(rates=clean_rates(rates), source=source,
                                    published_by=published_by)


def current() -> RateSheet:
    """The newest sheet. The first payment on an empty database publishes the reference."""
    sheet = RateSheet.objects.first()
    if sheet is None:
        sheet = publish(reference_rates(), source="reference rates (first use)",
                        published_by="system")
    return sheet


@dataclass(frozen=True)
class Lock:
    """What a payment stores when it is created."""
    rate: Decimal  # SGD per one unit; 1 for SGD
    sheet_id: str  # "" for SGD
    fee_bps: int  # 0 for SGD


def lock(currency: str) -> Lock:
    if currency == SETTLEMENT:
        return Lock(Decimal(1), "", 0)
    sheet = current()
    return Lock(sheet.rate(currency), sheet.public_id, settings.GATEWAY_RULES["FX_FEE_BPS"])


def to_sgd(amount: int, currency: str, rate: Decimal) -> int:
    """SGD cents for `amount` smallest units of `currency`, half up."""
    if currency == SETTLEMENT:
        return amount
    major = Decimal(amount) / (Decimal(10) ** minor_units(currency))
    return int((major * rate * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def fx_fee(sgd_cents: int, fee_bps: int) -> int:
    return (sgd_cents * fee_bps + 5000) // 10000


def sheet_to_dict(sheet: RateSheet) -> dict:
    from apps.common.timefmt import rfc3339

    return {"object": "fx_rates", "id": sheet.public_id, "base": SETTLEMENT,
            "published_at": rfc3339(sheet.published_at),
            "fee_percent": format(Decimal(settings.GATEWAY_RULES["FX_FEE_BPS"]) / 100, "f"),
            "rates": {code: sheet.rates[code] for code in FOREIGN}}
