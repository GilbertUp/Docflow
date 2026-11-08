"""Makes the dummy documents and sets up the demo organization. Safe to run again.

    .venv/Scripts/python demo/prepare.py

Needs the demo server and web client running (see README, "Try it without Docker").
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))
from tests.compression import scans  # noqa: E402
from tests.documents.files import text_pdf  # noqa: E402

BASE = "http://127.0.0.1:5173/api/v1"
OUT = ROOT / "demo" / "documents"

DOCUMENTS = {
    "invoice-ABC-1042.pdf": [
        "TAX INVOICE   Supplier ABC Ltd, Kigali   Invoice no INV-1042   "
        "Date 2026-11-02   Due 2026-11-30   Consulting services, October   Total 1,042.50 USD"
    ],
    "invoice-XYZ-2210.pdf": [
        "TAX INVOICE   Supplier XYZ Stationery, Kigali   Invoice no INV-2210   "
        "Date 2026-11-05   Due 2026-12-05   Office supplies and printer paper   Total 318.00 USD"
    ],
    "purchase-order-77.pdf": [
        "PURCHASE ORDER 77   Buyer RD Tech   Supplier ABC Ltd   "
        "Ordered 2026-10-20   Consulting services, October   Agreed amount 1,042.50 USD"
    ],
    "board-minutes-november.pdf": [
        "MINUTES OF THE BOARD MEETING, 6 November 2026, Kigali.   "
        "Agenda: annual budget, hiring plan, office move.   Confidential."
    ],
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, pages in DOCUMENTS.items():
        (OUT / name).write_bytes(text_pdf(pages))
    # A scan of a delivery note, stored the heavy way, so that compressing it shows a big saving.
    (OUT / "scan-delivery-note.pdf").write_bytes(
        scans.grey_flate_pdf(
            [scans.text_page(11), scans.text_page(12), scans.text_page(13)]
        )
    )
    print(f"Wrote {len(DOCUMENTS) + 1} dummy documents to {OUT}")

    creds = (ROOT / ".data" / "demo" / "credentials.txt").read_text(encoding="utf-8")
    found = re.search(r"admin@example.com\s+/\s+(\S+)", creds)
    if not found:
        print("Could not find the admin sign-in in .data/demo/credentials.txt")
        return 1
    client = httpx.Client(timeout=60)
    login = client.post(
        f"{BASE}/auth/login",
        json={
            "organization": "demo",
            "email": "admin@example.com",
            "password": found.group(1),
        },
    )
    login.raise_for_status()
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}

    def ensure(path: str, body: dict, name: str) -> str:
        listed = client.get(f"{BASE}/{path}", params={"include_archived": True}).json()
        for item in listed:
            if item["name"] == name:
                print(f"  already there: {name}")
                return str(item["id"])
        made = client.post(f"{BASE}/{path}", json=body, headers=headers)
        if made.status_code >= 400:
            print(f"  FAILED {name}: {made.status_code} {made.text[:200]}")
            return ""
        print(f"  created: {name}")
        return str(made.json()["id"])

    print("Setting up:")
    ensure(
        "document-types",
        {
            "name": "Supplier invoice",
            "description": "Invoices from suppliers",
            "keywords": ["invoice", "supplier", "total", "due"],
            "auto_accept_threshold": 0.5,
            "review_threshold": 0.2,
            "fields": [
                {
                    "key": "invoice_number",
                    "label": "Invoice number",
                    "data_type": "text",
                    "required": True,
                    "validation": {"pattern": r"INV-\d{4}"},
                    "extraction": {"labels": ["Invoice no"], "pattern": r"INV-\d{4}"},
                },
                {
                    "key": "due_date",
                    "label": "Due date",
                    "data_type": "date",
                    "extraction": {"labels": ["Due"]},
                },
                {
                    "key": "total",
                    "label": "Total",
                    "data_type": "number",
                    "required": True,
                    "validation": {"min": 0},
                    "extraction": {"labels": ["Total"]},
                },
            ],
        },
        "Supplier invoice",
    )
    people = {p["name"]: p["id"] for p in client.get(f"{BASE}/people").json()}
    ensure(
        "workflows",
        {
            "name": "Invoice approval",
            "description": "Finance check, then admin sign-off",
            "steps": [
                {
                    "name": "Finance check",
                    "kind": "approval",
                    "assignee_id": people["Felix"],
                    "due_days": 2,
                },
                {
                    "name": "Final approval",
                    "kind": "approval",
                    "assignee_id": people["Admin"],
                    "due_days": 3,
                },
            ],
        },
        "Invoice approval",
    )
    for name, text, color in (
        ("Approved", "APPROVED", "green"),
        ("Paid", "PAID", "blue"),
    ):
        stamps = client.get(f"{BASE}/stamps", params={"include_archived": True}).json()
        if any(s["name"] == name for s in stamps):
            print(f"  already there: stamp {name}")
        else:
            client.post(
                f"{BASE}/stamps",
                json={"name": name, "text": text, "color": color},
                headers=headers,
            ).raise_for_status()
            print(f"  created: stamp {name}")
    ensure(
        "forms",
        {
            "name": "Leave request",
            "description": "Ask for time off",
            "fields": [
                {
                    "key": "days",
                    "label": "Days off",
                    "data_type": "number",
                    "required": True,
                    "validation": {"min": 1, "max": 30},
                },
                {
                    "key": "first_day",
                    "label": "First day",
                    "data_type": "date",
                    "required": True,
                },
                {
                    "key": "kind",
                    "label": "Kind",
                    "data_type": "choice",
                    "choices": [
                        {"value": "annual", "label": "Annual"},
                        {"value": "sick", "label": "Sick"},
                    ],
                },
            ],
        },
        "Leave request",
    )
    ensure(
        "retention-classes",
        {
            "name": "Invoices 7 years",
            "description": "Keep supplier invoices for seven years",
            "duration_days": 2555,
            "trigger": "created",
        },
        "Invoices 7 years",
    )
    ensure(
        "retention-classes",
        {
            "name": "Short-lived notes (1 day)",
            "description": "For showing disposal in a demo. Real classes are measured in years.",
            "duration_days": 1,
            "trigger": "created",
        },
        "Short-lived notes (1 day)",
    )
    ensure(
        "legal-holds",
        {"name": "Audit 2026", "reason": "External audit of supplier payments"},
        "Audit 2026",
    )
    client.put(
        f"{BASE}/sla-policy",
        json={
            "reminder_before_hours": 24,
            "escalate_after_hours": 24,
            "escalate_to_user_id": people["Admin"],
        },
        headers=headers,
    )
    print("  deadlines: remind 24h before, escalate to Admin 24h after")

    # Prove the type and fields fill in, using a throwaway document, then say so.
    cabinets = client.get(f"{BASE}/cabinets").json()["items"]
    finance = next(c for c in cabinets if c["name"] == "Finance")
    folders = client.get(f"{BASE}/cabinets/{finance['id']}/children").json()["folders"]
    folder = next(f for f in folders if f["name"] == "Supplier Invoices")["id"]
    probe = text_pdf(
        [
            "TAX INVOICE   Supplier Probe Ltd   Invoice no INV-9999   Due 2026-12-31   Total 1.00"
        ]
    )
    up = client.post(
        f"{BASE}/folders/{folder}/documents",
        params={"filename": "check-setup.pdf"},
        content=probe,
        headers={**headers, "Content-Type": "application/octet-stream"},
    )
    doc = up.json()["document"]["id"]
    filled = {}
    for _ in range(25):
        time.sleep(1)
        shown = client.get(f"{BASE}/documents/{doc}/suggestions").json()
        if shown.get("fields"):
            filled = {f["key"]: f["value"] for f in shown["fields"]}
            break
    print(
        f"Setup check: type proposed and values found: {filled or 'NOTHING (check the log)'}"
    )
    return 0 if filled else 2


if __name__ == "__main__":
    raise SystemExit(main())
