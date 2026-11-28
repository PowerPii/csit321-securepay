"""The runner's plans: right shape, valid demo cards, no network."""
import random
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scenarios import ATTACK_BIN, luhn_ok, plan


def test_card_testing_is_a_swarm_that_retries_each_card():
    attempts = plan("card_testing", random.Random(1), "rt_abcdef", bots=10, tries=6)
    assert len(attempts) == 10 * 2 * 6
    assert len({a.ip for a in attempts}) == 10  # one address per bot
    per_card = Counter(a.card for a in attempts)
    assert set(per_card.values()) == {6}
    assert all(luhn_ok(a.card) and 50 <= a.amount <= 300 for a in attempts)


def test_bin_attack_uses_each_card_address_and_email_once():
    attempts = plan("bin_attack", random.Random(2), "rt_abcdef", count=50)
    assert len(attempts) == 50
    for field in ("card", "ip", "email"):
        assert len({getattr(a, field) for a in attempts}) == 50
    assert all(a.card.startswith(ATTACK_BIN) and luhn_ok(a.card) for a in attempts)


def test_background_shoppers_are_mixed_in_and_labelled_legit():
    attempts = plan("bin_attack", random.Random(3), "rt_abcdef", count=40, legit_count=8)
    kinds = [a.kind for a in attempts]
    assert kinds.count("legit") == 8 and kinds.count("attack") == 40
    assert kinds[0] == "attack" and "legit" in kinds[: len(kinds) // 2]


def test_plans_are_repeatable_with_a_seed():
    one = plan("card_testing", random.Random(7), "rt_x", bots=3, tries=2)
    two = plan("card_testing", random.Random(7), "rt_x", bots=3, tries=2)
    assert one == two
