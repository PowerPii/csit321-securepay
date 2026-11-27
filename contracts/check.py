"""Check the contracts: schemas are valid, examples match them, and the contract agrees with the code.

Run from the repository root:  python contracts/check.py
Needs: jsonschema, PyYAML, openapi-spec-validator (installed by the CI "contracts" job).
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
errors: list[str] = []


def load(path: Path):
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text) if path.suffix in {".yaml", ".yml"} else json.loads(text)


# Every schema and OpenAPI document is registered under its file URI; JSON Schemas also under $id.
docs: dict[Path, object] = {}
registry = Registry()
for path in sorted([*ROOT.glob("schemas/*.json"), *ROOT.glob("events/*.json"), *ROOT.glob("openapi/*.yaml")]):
    doc = load(path)
    docs[path] = doc
    res = Resource.from_contents(doc, default_specification=DRAFT202012)
    registry = registry.with_resource(path.as_uri(), res)
    if isinstance(doc, dict) and "$id" in doc:
        registry = registry.with_resource(doc["$id"], res)

for path, doc in docs.items():
    if path.suffix == ".json":
        try:
            Draft202012Validator.check_schema(doc)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.relative_to(REPO)}: invalid schema: {exc}")

try:
    from openapi_spec_validator import validate
    from openapi_spec_validator.readers import read_from_filename

    for path in sorted(ROOT.glob("openapi/*.yaml")):
        spec, base_uri = read_from_filename(str(path))
        try:
            validate(spec, base_uri=base_uri)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path.relative_to(REPO)}: invalid OpenAPI: {str(exc).splitlines()[0]}")
except ImportError:
    print("openapi-spec-validator not installed: skipped OpenAPI validation")


def ref(rel: str, pointer: str = "") -> dict:
    return {"$ref": (ROOT / rel).as_uri() + (f"#{pointer}" if pointer else "")}


MERCHANT = "openapi/merchant-api.v1.yaml"
GUEST = "openapi/guest-status.v1.yaml"
SIM = "openapi/simulator.yaml"
SIM_OP = "schemas/simulator-operation.schema.json"
LIST_REFUNDS = "/paths/~1v1~1payments~1{payment_id}~1refunds/get/responses/200/content/application~1json/schema"
EXAMPLES = {
    "api/create-payment.201.json": ref("schemas/payment.schema.json"),
    "api/get-payment-authorised.200.json": ref("schemas/payment.schema.json"),
    "api/get-payment-captured-with-refund.200.json": ref("schemas/payment.schema.json"),
    "api/capture-no-answer.202.json": ref("schemas/payment.schema.json"),
    "api/create-refund-unresolved.201.json": ref("schemas/refund.schema.json"),
    "api/list-refunds.200.json": ref(MERCHANT, LIST_REFUNDS),
    "api/error-idempotency-key-reused.409.json": ref("schemas/error.schema.json"),
    "api/error-refund-exceeds.422.json": ref("schemas/error.schema.json"),
    "api/get-balance.200.json": ref("schemas/balance.schema.json"),
    "api/create-payment-thb.201.json": ref("schemas/payment.schema.json"),
    "api/get-fx-rates.200.json": ref("schemas/fx-rates.schema.json"),
    "api/create-payout.201.json": ref("schemas/payout.schema.json"),
    "api/error-insufficient-balance.422.json": ref("schemas/error.schema.json"),
    "api/error-processor-rejected.409.json": ref("schemas/error.schema.json"),
    "events/payment.authorised.json": ref("events/payment.authorised.schema.json"),
    "events/payment.blocked.json": ref("events/payment.blocked.schema.json"),
    "events/refund.unresolved.json": ref("events/refund.unresolved.schema.json"),
    "guest-status/code-requested.202.json": ref(GUEST, "/components/schemas/CodeRequested"),
    "guest-status/code-incorrect.400.json": ref(GUEST, "/components/schemas/CodeError"),
    "guest-status/guest-payment.200.json": ref(GUEST, "/components/schemas/GuestPaymentView"),
    "simulator/authorise-approved.200.json": ref(SIM_OP),
    "simulator/authorise-declined.200.json": ref(SIM_OP),
    "simulator/capture-partial-approved.200.json": ref(SIM_OP),
    "simulator/status-no-record.200.json": ref(SIM_OP),
    "simulator/error-operation-retired.409.json": ref(SIM, "/components/schemas/ConflictError"),
    "simulator/notification.operation-recorded.json": ref(SIM, "/components/schemas/Notification"),
}

found = {p.relative_to(ROOT / "examples").as_posix() for p in (ROOT / "examples").rglob("*.json")}
for missing in sorted(found - EXAMPLES.keys()):
    errors.append(f"examples/{missing}: no schema mapped in check.py")
for name, schema in EXAMPLES.items():
    validator = Draft202012Validator(schema, registry=registry)
    for err in validator.iter_errors(load(ROOT / "examples" / name)):
        errors.append(f"examples/{name}: {'/'.join(map(str, err.absolute_path)) or '<root>'}: {err.message}")

# An example event of one type must not pass as another type (the per-type schemas really narrow).
wrong = load(ROOT / "examples/events/payment.authorised.json")
if Draft202012Validator(ref("events/payment.blocked.schema.json"), registry=registry).is_valid(wrong):
    errors.append("events/payment.blocked.schema.json accepts a payment.authorised event")

# A no_record entry must not carry an outcome, and a decline must carry a reason.
sim_op = Draft202012Validator(ref(SIM_OP), registry=registry)
bad_no_record = {**load(ROOT / "examples/simulator/status-no-record.200.json"), "amount": 4590}
if sim_op.is_valid(bad_no_record):
    errors.append("simulator-operation.schema.json accepts a no_record entry with an amount")
bad_decline = {**load(ROOT / "examples/simulator/authorise-declined.200.json"), "decline_reason": None}
if sim_op.is_valid(bad_decline):
    errors.append("simulator-operation.schema.json accepts a declined operation without a reason")

# Event types: the envelope enum and the files in events/ must agree.
envelope_types = set(docs[ROOT / "schemas/event.schema.json"]["properties"]["type"]["enum"])
file_types = {p.name.removesuffix(".schema.json") for p in ROOT.glob("events/*.schema.json")}
if envelope_types != file_types:
    errors.append(f"event types differ: envelope only {envelope_types - file_types}, files only {file_types - envelope_types}")


# Payment statuses and block reasons must match gateway/apps/payments/states.py.
def enum_values(module: Path, class_name: str) -> set[str]:
    tree = ast.parse(module.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return {
                stmt.value.value
                for stmt in node.body
                if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Constant)
            }
    raise LookupError(class_name)


states = REPO / "gateway/apps/payments/states.py"
payment_defs = docs[ROOT / "schemas/payment.schema.json"]["$defs"]
if set(payment_defs["PaymentStatus"]["enum"]) != enum_values(states, "PaymentStatus"):
    errors.append("PaymentStatus in payment.schema.json differs from gateway/apps/payments/states.py")
if not enum_values(states, "BlockReason") <= set(payment_defs["OutcomeReason"]["enum"]):
    errors.append("OutcomeReason in payment.schema.json is missing a BlockReason from states.py")

# The public, bundled OpenAPI file (website + SDK types) is regenerated from the YAML above,
# is a valid OpenAPI document and has no reference left outside itself.
sys.path.insert(0, str(ROOT))
import bundle_openapi  # noqa: E402

public_spec = bundle_openapi.bundle()
for path, text in bundle_openapi.outputs(public_spec).items():
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        errors.append(f"{path.relative_to(REPO)} is out of date: run python contracts/bundle_openapi.py")
external = sorted({r for r in re.findall(r'"\$ref": "([^"]+)"', json.dumps(public_spec)) if not r.startswith("#/")})
if external:
    errors.append(f"bundled OpenAPI still has external references: {external}")
try:
    from openapi_spec_validator import validate as validate_openapi

    validate_openapi(public_spec)
except ImportError:
    pass
except Exception as exc:  # noqa: BLE001
    errors.append(f"bundled OpenAPI is invalid: {str(exc).splitlines()[0]}")

if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"contracts OK: {len(docs)} schema/OpenAPI files, {len(EXAMPLES)} examples")
