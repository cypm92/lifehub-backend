"""Proyección de solo lectura de movimientos bancarios, sin datos sensibles."""
import re
import unicodedata
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from app import banking_models as bm
from app.services.banking_sync import counterpart


def normalized_text(value):
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKD", value.casefold())
    return " ".join(re.sub(r"[^\w]+", " ", "".join(c for c in text if not unicodedata.combining(c))).split())


def iban(value):
    return re.sub(r"\s+", "", value).upper() if isinstance(value, str) else ""


def amount_value(value):
    try:
        result = Decimal(str(value))
        return float(result) if result.is_finite() else None
    except (InvalidOperation, ValueError, TypeError, OverflowError):
        return None


def current_balance(data):
    """Preferir saldo disponible, después contable; nunca sumar saldos alternativos."""
    if not isinstance(data, dict) or not isinstance(data.get("balances"), list):
        return None
    balances = [b for b in data["balances"] if isinstance(b, dict)]
    balances.sort(key=lambda b: {"ITAV": 0, "CLAV": 1, "ITBD": 2, "CLBD": 3}.get(b.get("balance_type"), 4))
    for balance in balances:
        value = balance.get("balance_amount")
        if isinstance(value, dict):
            amount = amount_value(value.get("amount"))
            if amount is not None:
                return {"amount": amount, "currency": value.get("currency")}
    return None


def movement_date(raw):
    for key in ("booking_date", "value_date"):
        value = raw.get(key)
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                pass
    return None


def account_name(data):
    for field in ("details", "product"):
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    kind = data.get("cash_account_type")
    return {"CACC": "Cuenta corriente", "SVGS": "Ahorro"}.get(kind, "Cuenta")


def concept_value(raw):
    value = raw.get("remittance_information")
    if isinstance(value, list):
        value = " ".join(part.strip() for part in value if isinstance(part, str) and part.strip())
    return " ".join(value.split()) if isinstance(value, str) else ""


def paired_transfers(rows):
    """Emparejamiento voraz determinista; un movimiento participa como máximo una vez."""
    buckets = {}
    parsed = []
    for tx in rows:
        raw = tx.raw_data if isinstance(tx.raw_data, dict) else {}
        day = movement_date(raw)
        amount = raw.get("transaction_amount")
        if not day or not isinstance(amount, dict):
            continue
        currency, direction = amount.get("currency"), raw.get("credit_debit_indicator")
        if not isinstance(currency, str) or not currency.strip() or direction not in ("CRDT", "DBIT"):
            continue
        try:
            value = abs(Decimal(str(amount.get("amount"))))
            if not value.is_finite() or not value:
                continue
        except (InvalidOperation, ValueError, TypeError):
            continue
        key = (value, currency.strip().upper(), direction, day)
        buckets.setdefault(key, []).append(tx)
        parsed.append((day, tx.id, tx, value, currency.strip().upper(), direction))
    paired = set()
    for day, _, tx, value, currency, direction in sorted(parsed):
        if tx.id in paired:
            continue
        opposite = "CRDT" if direction == "DBIT" else "DBIT"
        candidates = []
        for delta in (0, -1, 1):
            for other in buckets.get((value, currency, opposite, day + timedelta(days=delta)), []):
                if other.id not in paired and other.account_id != tx.account_id:
                    candidates.append((abs(delta), other.id, other))
        if candidates:
            other = min(candidates)[2]
            paired.update((tx.id, other.id))
    return paired


def list_movements(db, user_id, connection_id, days):
    owned = (db.query(bm.BankAccount, bm.BankConnection).join(bm.BankConnection)
             .filter(bm.BankConnection.user_id == user_id).all())
    # Todas estas reglas son heurísticas: homónimos, conceptos ambiguos o importes
    # coincidentes pueden producir falsos positivos; no alteran presupuestos ni asignaciones.
    names, ibans, account_references = set(), set(), {"cuenta remunerada"}
    for account, _ in owned:
        data = account.data if isinstance(account.data, dict) else {}
        names.add(normalized_text(data.get("name")))
        for field in ("details", "product", "name", "currency"):
            reference = normalized_text(data.get(field))
            if reference:
                account_references.add(reference)
        account_id = data.get("account_id")
        if isinstance(account_id, dict):
            ibans.add(iban(account_id.get("iban")))
    names.discard("")
    ibans.discard("")
    all_ids = [a.id for a, _ in owned]
    all_rows = db.query(bm.BankTransaction).filter(bm.BankTransaction.account_id.in_(all_ids)).all() if all_ids else []
    paired = paired_transfers(all_rows)
    selected = [(a, c) for a, c in owned if connection_id is None or c.id == connection_id]
    accounts = []
    for account, connection in selected:
        data = account.data if isinstance(account.data, dict) else {}
        accounts.append({"id": account.id, "connection_id": connection.id, "bank_name": connection.bank_name,
                         "name": account_name(data), "type": data.get("cash_account_type"),
                         "currency": data.get("currency"), "balance": current_balance(account.balances)})
    by_id = {a.id: (a, c) for a, c in selected}
    rows = [tx for tx in all_rows if tx.account_id in by_id]
    assignments = {a.transaction_id: a for a in db.query(bm.BankAssignment).filter(
        bm.BankAssignment.user_id == user_id, bm.BankAssignment.transaction_id.in_([r.id for r in rows])).all()} if rows else {}
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    movements = []
    for tx in rows:
        raw = tx.raw_data if isinstance(tx.raw_data, dict) else {}
        day = movement_date(raw)
        if day is None or not start <= day <= today:
            continue
        amount_data = raw.get("transaction_amount") if isinstance(raw.get("transaction_amount"), dict) else {}
        amount = amount_value(amount_data.get("amount"))
        indicator = raw.get("credit_debit_indicator")
        if amount is not None:
            amount = -abs(amount) if indicator == "DBIT" else abs(amount) if indicator == "CRDT" else amount
        concept = concept_value(raw)
        party = counterpart(raw)
        party = party.strip() if isinstance(party, str) else ""
        party_iban = iban(counterpart(raw, account=True))
        party_normalized = normalized_text(party)
        concept_match = re.match(r"^(?:to|from)\s+(.+)$", normalized_text(concept))
        target = concept_match.group(1) if concept_match else ""
        references_account = bool(target and any(target == ref or target.startswith(ref + " ") for ref in account_references))
        normalized_concept = normalized_text(concept)
        is_refund = (normalized_concept.startswith("refund from ")
                     or "devolucion" in normalized_concept or "reembolso" in normalized_concept)
        internal = bool(tx.id in paired or references_account or party_normalized == "cuenta remunerada"
                        or (not is_refund and party_normalized and party_normalized in names)
                        or (party_iban and party_iban in ibans))
        account, connection = by_id[tx.account_id]
        data = account.data if isinstance(account.data, dict) else {}
        assignment = assignments.get(tx.id)
        movements.append({"id": tx.id, "date": day.isoformat(), "amount": amount,
                          "currency": amount_data.get("currency"), "direction": indicator,
                          "status": raw.get("status"), "counterparty": party or concept or None,
                          "concept": concept, "account_id": account.id, "account_name": account_name(data),
                          "account_type": data.get("cash_account_type"), "connection_id": connection.id,
                          "bank_name": connection.bank_name, "is_internal_transfer": internal,
                          "assignment": {"id": assignment.id, "transaction_id": tx.id,
                                         "destination_type": assignment.destination_type,
                                         "destination_id": assignment.destination_id} if assignment else None})
    movements.sort(key=lambda item: (item["date"], item["id"]), reverse=True)
    return {"movements": movements, "accounts": accounts}
