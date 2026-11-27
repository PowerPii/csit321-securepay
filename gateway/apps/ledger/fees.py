"""How a captured amount splits into fee, rolling reserve and the merchant's net.

Integer cents only. The fee rounds half up; the reserve rounds down; the merchant's net takes
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


def split(gross: int) -> Split:
    if gross <= 0:
        raise ValueError("gross must be positive")
    rules = settings.GATEWAY_RULES
    fee = (gross * rules["FEE_PERCENT_BPS"] + 5000) // 10000 + rules["FEE_FIXED_CENTS"]
    fee = min(fee, gross)
    reserve = min(gross * rules["RESERVE_BPS"] // 10000, gross - fee)
    return Split(gross=gross, fee=fee, reserve=reserve, net=gross - fee - reserve)
