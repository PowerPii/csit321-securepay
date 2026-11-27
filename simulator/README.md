# Processing simulator

Stands in for the card network/issuer: enforces unique operation ids, records ledger entries and injects faults (lost responses, delays, declines).

v2: one codebase, three network roles (`contracts/NETWORK-CONTRACT.md`). `SIM_ROLE=acquirer`
serves the `/sim` API and records operations; `switch` and `issuer` serve `/net/switch` and
`/net/issuer`. Each authorisation goes acquirer to switch to issuer as an ISO 8583-style 0100
and comes back as a 0110, and the operation keeps the hop trace in `network`. With the default
`SIM_ROLE=all` (local runs, tests) the three run in one process.

## Run locally

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install --require-hashes --no-deps -r requirements-dev.lock
export DJANGO_DEBUG=1
python manage.py migrate
python manage.py runserver 8001
```

Needs PostgreSQL; the easiest way is `docker compose -f ../deploy/compose.yaml up db`.

Built (sprint 2, `contracts/SIMULATOR-CONTRACT.md`): `POST /sim/authorise` with the test cards
in section 5 (including the new lost-request card 4000 0000 0000 0135 and expired cards), and
`GET /sim/operations/{id}` with the no-record rule (section 6). One record per `operation_id`;
replays, conflicts and retired IDs answer as section 3 says. Replies are signed with
`simulator_signing_secret`; requests need `Bearer <simulator_api_key>`. The hanging cards hold
the connection `SIM_HOLD_SECONDS` (15) and then send an unsigned 504 in place of hanging up.
Capture, cancel, refund and the fault controls come in sprints 3 and 5.
