"""DRF rate limits whose counters stay exact when several workers count at once.

DRF's throttles read a key's request history from the cache, add the new request and write
it back. With several gunicorn workers sharing the database cache (settings.CACHES), two
requests for the same key can both read the old history, and one of the two writes is lost:
a burst could get a little over the limit (Codex release review, 18 Nov 2026). Here the
read-modify-write for one key runs under a Postgres advisory lock on that key, so requests
for the same key take turns and requests for different keys never wait for each other.

Use these classes instead of rest_framework.throttling's (same names, same settings).
"""
import hashlib
from contextlib import contextmanager

from django.db import connections, router
from rest_framework import throttling


def _cache_db() -> str:
    from django.core.cache import cache

    # The database cache's table lives in the database its router picks for writes.
    model = getattr(cache, "cache_model_class", None)
    return router.db_for_write(model) if model is not None else "default"


def _lock_id(key: str) -> int:
    """A signed 64-bit number for pg_advisory_lock, the same in every process."""
    return int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big",
                          signed=True)


@contextmanager
def counter_lock(key: str):
    """Only one process at a time runs the block for `key` (Postgres; elsewhere no lock).

    A session lock, not a transaction lock, so it is released straight after the block even
    inside an outer transaction (tests, or a view that is atomic), and with the connection
    if the process dies."""
    connection = connections[_cache_db()]
    if connection.vendor != "postgresql":
        yield
        return
    lock_id = _lock_id(key)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_lock(%s)", [lock_id])
    try:
        yield
    finally:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_unlock(%s)", [lock_id])


class SimpleRateThrottle(throttling.SimpleRateThrottle):
    def allow_request(self, request, view):
        if self.rate is None:
            return True
        key = self.get_cache_key(request, view)
        if key is None:
            return True
        with counter_lock(key):
            return super().allow_request(request, view)


# ScopedRateThrottle sets its rate inside allow_request and then calls the next class, so the
# locked SimpleRateThrottle has to come after it.
class AnonRateThrottle(throttling.AnonRateThrottle, SimpleRateThrottle):
    def get_ident(self, request):
        # v2 red team: each bot of a live run is limited on its own address (apps/redteam),
        # so a swarm from one laptop meets the same per-IP limit a real swarm would.
        from apps.redteam.service import bot_ip

        ip = bot_ip(request)
        return f"red-team:{ip}" if ip else super().get_ident(request)


class ScopedRateThrottle(throttling.ScopedRateThrottle, SimpleRateThrottle):
    pass
