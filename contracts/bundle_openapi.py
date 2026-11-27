"""Bundle the merchant API contract into one public OpenAPI file.

`openapi/merchant-api.v1.yaml` points at the shared JSON Schemas in `schemas/` and uses
requirement IDs (FR-..., UC-...) and repository paths that mean nothing outside the team.
This script writes a single self-contained copy for merchants:

  - every `../schemas/*.json` reference is inlined under `components/schemas`
    (Payment, Refund, Event, Error, plus the `$defs` they share: PaymentStatus, LineItem, ...);
  - requirement IDs in brackets are dropped, and pointers to API-CONTRACT.md become links to
    the public guides;
  - `servers` names the live demo gateway.

Outputs (all generated, never edit by hand):
  contracts/generated/merchant-api.v1.json           read by the SDK's type generator (sdk/node)
  frontend/website/public/openapi/merchant-api.v1.json and .yaml   served on the website

Run from the repository root:  python contracts/bundle_openapi.py
`--check` writes nothing and fails if an output is out of date (contracts/check.py runs it in CI).
"""
from __future__ import annotations

import copy
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
SOURCE = ROOT / "openapi" / "merchant-api.v1.yaml"
OUT_JSON = [ROOT / "generated" / "merchant-api.v1.json", REPO / "frontend/website/public/openapi/merchant-api.v1.json"]
OUT_YAML = [REPO / "frontend/website/public/openapi/merchant-api.v1.yaml"]

GATEWAY = "https://gateway.securepaysg.me"
GUIDES = "https://www.securepaysg.me/developers"

# File name -> component name. Their `$defs` are lifted next to them under the same names.
SCHEMA_FILES = {
    "payment.schema.json": "Payment",
    "refund.schema.json": "Refund",
    "balance.schema.json": "Balance",
    "payout.schema.json": "Payout",
    "fx-rates.schema.json": "FxRates",
    "event.schema.json": "Event",
    "error.schema.json": "Error",
}

DESCRIPTION = f"""\
The SecurePay merchant API: create a payment, send the customer to the hosted checkout,
then capture, cancel or refund it, and receive signed webhooks as its status changes.

- **Base URL**: `{GATEWAY}` (a live demo: every payment is simulated, no real money moves).
- **Authentication**: `Authorization: Bearer sp_test_...` (test mode) or `sp_live_...` (live
  mode), from the merchant dashboard. Every object says which mode it belongs to in `livemode`.
- **Idempotency**: every `POST` needs an `Idempotency-Key` header, kept for 24 hours.
- **Signatures**: every reply to a valid key, and every webhook, carries
  `SecurePay-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "t." + raw body>`, made with
  your webhook signing secret (`whsec_...`).
- **Money**: integer cents in SGD (50 to 1,000,000).

Guides (lifecycle, retries, errors, test cards): {GUIDES}.
A TypeScript client is on npm as `securepaysg`.

SecurePay is a student project (CSIT321 group FYP-26-S4-36, University of Wollongong). It is not a licensed
payment service and must never be used with real card data.
"""

# "(UC-53, FR-MER-11)", "(FR-TXN-06 to FR-TXN-08)", "(NFR-AVA-02)" ...
REQ_ID = r"(?:UC|FR|NFR)-(?:[A-Z]+-)?\d+[a-z]?"
REQ_BRACKET = re.compile(rf"\s*\((?:{REQ_ID})(?:(?:,\s*|\s+to\s+|\s+and\s+){REQ_ID})*\)")
REQ_INLINE = re.compile(rf"\s*\((?:{REQ_ID})(?:,\s*)?([^)]*)\)")  # "(FR-ADM-07, if set)" -> " (if set)"
CONTRACT_SECTION = re.compile(r"(?:contracts/)?API-CONTRACT\.md(?: section (\d+))?")
REQ_TRAILING = re.compile(rf",\s*{REQ_ID}(?=\))")  # "(code x, FR-MER-08)" -> "(code x)"
SECTION_ANCHORS = {"4": "errors", "5": "idempotency", "6": "webhooks", "7": "webhooks"}

# Notes for the team that a merchant does not need, and local addresses in the examples.
PUBLIC_TEXT = [
    (" (GATEWAY_RULES MIN/MAX_AMOUNT_CENTS)", ""),
    ("Mirrors gateway/apps/payments/states.py (Figure 2.2.2.1).", "Where the payment is in its lifecycle. See the guides for the diagram."),
    ("The first six are BlockReason in states.py. ", "The first six are fraud or limit blocks. "),
    (" (wireframe C3)", ""),
    (" The URS states 'requested' and 'rejected' exist only in the dashboard: a rejected request is answered with an error and creates no refund object.",
     " A refund request the gateway refuses is answered with an error and creates no refund object."),
]
PUBLIC_STRINGS = [
    ("https://shop.localhost/", "https://shop.example.com/"),
    ("https://localhost:8443/", GATEWAY + "/"),
]


def clean_text(text: str) -> str:
    for old, new in PUBLIC_TEXT:
        text = text.replace(old, new)
    text = REQ_BRACKET.sub("", text)
    text = REQ_TRAILING.sub("", text)
    text = REQ_INLINE.sub(lambda m: f" ({m.group(1)})" if m.group(1).strip() else "", text)

    def contract_link(m: re.Match) -> str:
        anchor = SECTION_ANCHORS.get(m.group(1) or "", "")
        return f"[the guides]({GUIDES}{'#' + anchor if anchor else ''})"

    return CONTRACT_SECTION.sub(contract_link, text)


def walk(node, fn):
    """Apply fn to every dict in the tree (depth first), returning the transformed tree."""
    if isinstance(node, list):
        return [walk(n, fn) for n in node]
    if isinstance(node, dict):
        return fn({k: walk(v, fn) for k, v in node.items()})
    return node


def bundle() -> dict:
    spec = yaml.safe_load(SOURCE.read_text(encoding="utf-8"))
    components = spec.setdefault("components", {})
    schemas = components.setdefault("schemas", {})

    lifted: dict[str, dict] = {}
    for filename, name in SCHEMA_FILES.items():
        doc = json.loads((ROOT / "schemas" / filename).read_text(encoding="utf-8"))
        for def_name, def_schema in doc.pop("$defs", {}).items():
            if def_name in lifted or def_name in schemas:
                raise SystemExit(f"bundle: $defs name {def_name!r} is used twice")
            lifted[def_name] = def_schema
        for key in ("$schema", "$id"):
            doc.pop(key, None)
        lifted[name] = doc

    def rewrite_ref(d: dict) -> dict:
        ref = d.get("$ref")
        if isinstance(ref, str):
            if ref.startswith("#/$defs/"):
                d["$ref"] = "#/components/schemas/" + ref.removeprefix("#/$defs/")
            else:
                filename = ref.rsplit("/", 1)[-1]
                if filename in SCHEMA_FILES:
                    d["$ref"] = "#/components/schemas/" + SCHEMA_FILES[filename]
                elif not ref.startswith("#/"):
                    raise SystemExit(f"bundle: cannot inline $ref {ref!r}")
        return d

    spec = walk(spec, rewrite_ref)
    lifted = walk(lifted, rewrite_ref)
    # Public order: the objects first, then the request bodies the YAML defines.
    spec["components"]["schemas"] = {**lifted, **spec["components"]["schemas"]}

    def clean(d: dict) -> dict:
        for key in ("summary", "description"):
            if isinstance(d.get(key), str):
                d[key] = clean_text(d[key])
        return d

    spec = walk(spec, clean)

    def public_string(node):
        if isinstance(node, str):
            for old, new in PUBLIC_STRINGS:
                node = node.replace(old, new)
            return node
        if isinstance(node, list):
            return [public_string(n) for n in node]
        if isinstance(node, dict):
            return {k: public_string(v) for k, v in node.items()}
        return node

    spec = public_string(spec)
    spec["info"] = {
        "title": "SecurePay merchant API",
        "version": spec["info"]["version"].removesuffix("-draft"),
        "summary": spec["info"].get("summary", ""),
        "description": DESCRIPTION,
        "contact": {"name": "SecurePay developers", "url": GUIDES},
    }
    spec["servers"] = [{"url": GATEWAY, "description": "Live demo gateway (simulated payments)"}]
    spec["externalDocs"] = {"description": "Guides", "url": GUIDES}
    spec["tags"] = [
        {"name": "Payments", "description": "Create a payment, check it, then capture or cancel the authorisation."},
        {"name": "Refunds", "description": "Full or partial refunds of a captured payment."},
    ]
    return spec


def outputs(spec: dict) -> dict[Path, str]:
    as_json = json.dumps(spec, indent=2, ensure_ascii=False) + "\n"
    as_yaml = "# Generated by contracts/bundle_openapi.py from contracts/openapi/merchant-api.v1.yaml. Do not edit.\n" + yaml.safe_dump(
        spec, sort_keys=False, allow_unicode=True, width=100
    )
    return {**{p: as_json for p in OUT_JSON}, **{p: as_yaml for p in OUT_YAML}}


def main(argv: list[str]) -> int:
    check = "--check" in argv
    stale = []
    for path, text in outputs(bundle()).items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        if check:
            stale.append(str(path.relative_to(REPO)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            print(f"wrote {path.relative_to(REPO)}")
    if stale:
        print("Out of date (run python contracts/bundle_openapi.py): " + ", ".join(stale))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
