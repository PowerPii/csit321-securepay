"""What each red-team scenario sends (docs/v2/RED-TEAM.md). Pure functions, no network.

Every attempt is one real test payment: created on the merchant API, then card details and
"pay" on the hosted checkout, as a bot would. Cards are simulator demo cards, never real ones.

card_testing   A swarm of bots checking stolen cards with small amounts. Each bot has its own
               address and retries each card a few times, so the card-attempt burst rule and
               the model's velocity features should start blocking it within seconds: the
               attack rate climbs and the block rate follows.
bin_attack     A quieter, distributed attack: card numbers enumerated from one BIN, each
               tried once, from a fresh address and email, with ordinary amounts. Today's
               per-card, per-email and per-IP features all look clean, so much of it slips
               through. This is the pattern the learning loop has to learn.
legit          Background shoppers, mixed into any run with --legit, so a false positive
               shows up on the live view too.
"""
from __future__ import annotations

import ipaddress
import random
from dataclasses import dataclass

APPROVE_PREFIXES = ("42424200", "55555500")  # simulator demo ranges (SIMULATOR-CONTRACT.md 5)
ATTACK_BIN = "42424200"
SCENARIOS = ("card_testing", "bin_attack")
# 100.64.0.0/10 (shared address space): never a real shopper's address, about 4 million.
BOT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


@dataclass(frozen=True)
class Attempt:
    kind: str  # attack | legit
    scenario: str
    bot: str
    ip: str
    card: str
    email: str
    amount: int  # SGD cents


def luhn_digit(body: str) -> str:
    total = 0
    for i, ch in enumerate(reversed(body)):
        d = int(ch)
        if i % 2 == 0:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return str((10 - total % 10) % 10)


def luhn_ok(number: str) -> bool:
    return luhn_digit(number[:-1]) == number[-1]


def card(rng: random.Random, prefix: str) -> str:
    body = prefix + "".join(str(rng.randrange(10)) for _ in range(15 - len(prefix)))
    return body + luhn_digit(body)


def addresses(rng: random.Random, count: int) -> list[str]:
    picked: set[int] = set()
    size = BOT_NETWORK.num_addresses
    while len(picked) < count:
        picked.add(rng.randrange(1, size - 1))
    return [str(BOT_NETWORK.network_address + n) for n in picked]


def card_testing(rng: random.Random, run: str, bots: int, tries: int) -> list[Attempt]:
    """`bots` bots; each checks two stolen cards, `tries` times each, S$0.50 to S$3."""
    out = []
    for i, ip in enumerate(addresses(rng, bots)):
        email = f"ct-{run[-6:]}-{i}@example.com"
        for _ in range(2):
            number = card(rng, rng.choice(APPROVE_PREFIXES))
            out += [Attempt("attack", "card_testing", f"bot-{i}", ip, number, email,
                            rng.choice([50, 100, 150, 200, 300])) for _ in range(tries)]
    # Bots run side by side: interleave them, keeping each bot's own order.
    return interleave(rng, out)


def bin_attack(rng: random.Random, run: str, count: int) -> list[Attempt]:
    """`count` cards from one BIN, each once, from a fresh address and email."""
    start = rng.randrange(10_000_000)
    ips = addresses(rng, count)
    out = []
    for i in range(count):
        body = f"{ATTACK_BIN}{(start + i * 7) % 10_000_000:07d}"
        out.append(Attempt("attack", "bin_attack", f"bot-{i}", ips[i], body + luhn_digit(body),
                           f"shop{rng.randrange(10**6)}.{run[-4:]}@example.com",
                           rng.choice([1990, 2490, 3590, 4990, 6990, 7990])))
    return out


def legit(rng: random.Random, run: str, count: int) -> list[Attempt]:
    ips = addresses(rng, count)
    return [Attempt("legit", "legit", f"shopper-{i}", ips[i],
                    card(rng, rng.choice(APPROVE_PREFIXES)),
                    f"shopper{i}.{run[-4:]}@example.com",
                    rng.choice([1290, 2490, 3990, 5900, 8900, 12900, 19900]))
            for i in range(count)]


def interleave(rng: random.Random, attempts: list[Attempt]) -> list[Attempt]:
    queues: dict[str, list[Attempt]] = {}
    for a in attempts:
        queues.setdefault(a.bot, []).append(a)
    out = []
    while queues:
        bot = rng.choice(sorted(queues))
        out.append(queues[bot].pop(0))
        if not queues[bot]:
            del queues[bot]
    return out


def plan(scenario: str, rng: random.Random, run: str, *, bots: int = 25, tries: int = 6,
         count: int = 120, legit_count: int = 0) -> list[Attempt]:
    if scenario == "card_testing":
        attacks = card_testing(rng, run, bots, tries)
    elif scenario == "bin_attack":
        attacks = bin_attack(rng, run, count)
    else:
        raise ValueError(f"unknown scenario {scenario!r}; use one of {', '.join(SCENARIOS)}")
    background = legit(rng, run, legit_count)
    if not background:
        return attacks
    # Spread the shoppers evenly through the attack.
    out, step = [], max(1, len(attacks) // (len(background) + 1))
    shoppers = iter(background)
    for i, a in enumerate(attacks):
        out.append(a)
        if (i + 1) % step == 0:
            nxt = next(shoppers, None)
            if nxt:
                out.append(nxt)
    out.extend(shoppers)
    return out
