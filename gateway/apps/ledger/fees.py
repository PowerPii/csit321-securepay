"""How a captured amount splits into fee, rolling reserve and the merchant's net.

Integer SGD cents only. The fees round half up; the reserve rounds down; the merchant's net takes
whatever is left, so the three always add up to the gross exactly.
"""
from dataclasses import dataclass

from django.conf import settings


@dataclass(frozen=True)
class Split:
    gross: int
    fee: int
    reserve: int
    net: int
    fx_fee: int = 0  # v2: payments in another currency (FX_FEE_BPS of the SGD gross)


def split(gross: int, fx_fee_bps: int = 0) -> Split:
    if gross <= 0:
        raise ValueError("gross must be positive")
    rules = settings.GATEWAY_RULES
    fee = (gross * rules["FEE_PERCENT_BPS"] + 5000) // 10000 + rules["FEE_FIXED_CENTS"]
    fee = min(fee, gross)
    fx_fee = min((gross * fx_fee_bps + 5000) // 10000, gross - fee)
    reserve = min(gross * rules["RESERVE_BPS"] // 10000, gross - fee - fx_fee)
    return Split(gross=gross, fee=fee, reserve=reserve, net=gross - fee - fx_fee - reserve,
                 fx_fee=fx_fee)
