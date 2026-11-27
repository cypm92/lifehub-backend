"""Sincronización y cobertura independientes del transporte y del presupuesto."""
import hashlib
import json
from datetime import datetime, timedelta, timezone

from app import banking_models as bm


def transaction_key(account_uid, raw):
    """IDs del banco preferidos; el hash de respaldo no distingue movimientos idénticos."""
    for field in ("transaction_id", "entry_reference"):
        value = raw.get(field)
        if isinstance(value, str) and value.strip():
            digest = hashlib.sha256(value.strip().encode("utf-8")).hexdigest()
            return f"{field}:{digest}"
    amount = raw.get("transaction_amount") or {}
    if not isinstance(amount, dict):
        amount = {}
    data = [account_uid, raw.get("booking_date") or raw.get("transaction_date") or raw.get("value_date"),
            amount.get("amount"), amount.get("currency"), raw.get("credit_debit_indicator"),
            raw.get("remittance_information") or raw.get("note") or ""]
    digest = hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return "fallback:" + digest


def sync_connection(db, connection, provider, session_id):
    """Una transacción por conexión: ante cualquier fallo no persiste un sync parcial.

    El llamador bloquea la fila de conexión hasta commit para serializar syncs.
    """
    session = provider.session(session_id)
    if not isinstance(session, dict) or session.get("status") != "AUTHORIZED":
        raise ValueError("Session not authorized")
    account_ids = session.get("accounts")
    if not isinstance(account_ids, list) or any(not isinstance(uid, str) or not uid for uid in account_ids):
        raise ValueError("Invalid account list")
    details = session.get("accounts_data") or []
    if not isinstance(details, list) or any(not isinstance(item, dict) or not isinstance(item.get("uid"), str) for item in details):
        raise ValueError("Invalid account data")
    by_uid = {item["uid"]: item for item in details}
    existing_accounts = {a.provider_uid: a for a in db.query(bm.BankAccount).filter_by(connection_id=connection.id).all()}
    since = (datetime.now(timezone.utc).date() - timedelta(days=90)).isoformat()
    created, updated = 0, 0
    for uid in dict.fromkeys(account_ids):
        account = existing_accounts.get(uid)
        if account is None:
            data = provider.account_details(uid)
            if not isinstance(data, dict) or (data.get("uid") and data["uid"] != uid):
                raise ValueError("Invalid account details")
            account = bm.BankAccount(connection_id=connection.id, provider_uid=uid,
                                     data={**data, **by_uid.get(uid, {})})
            db.add(account)
            db.flush()
        elif uid in by_uid:
            account.data = {**(account.data or {}), **by_uid[uid]}
        balances = provider.balances(uid)
        if not isinstance(balances, dict) or not isinstance(balances.get("balances"), list):
            raise ValueError("Invalid balances")
        account.balances = balances
        existing = {tx.dedupe_key: tx for tx in db.query(bm.BankTransaction).filter_by(account_id=account.id).all()}
        for raw in provider.transactions(uid, since):
            if not isinstance(raw, dict):
                raise ValueError("Invalid transaction")
            key = transaction_key(uid, raw)
            tx = existing.get(key)
            if tx is None:
                tx = bm.BankTransaction(account_id=account.id, dedupe_key=key, raw_data=raw)
                db.add(tx)
                existing[key] = tx
                created += 1
            elif tx.raw_data != raw:
                tx.raw_data = raw
                updated += 1
    connection.status = "active"
    return {"accounts": len(set(account_ids)), "created": created, "updated": updated, "date_from": since}


def filled(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, dict):
        return any(filled(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(filled(item) for item in value)
    return value is not None


def counterpart(raw, account=False):
    indicator = raw.get("credit_debit_indicator")
    suffix = "_account" if account else ""
    # Unknown direction: report either side rather than falsely report a missing field.
    names = (["creditor"] if indicator == "DBIT" else ["debtor"] if indicator == "CRDT" else ["creditor", "debtor"])
    for name in names:
        item = raw.get(name + suffix)
        if isinstance(item, dict):
            value = item.get("iban" if account else "name")
            if filled(value):
                return value
    return None


def field_presence(raw):
    return {
        "remittance": filled(raw.get("remittance_information")),
        "counterparty": filled(counterpart(raw)),
        "counterparty_iban": filled(counterpart(raw, account=True)),
        "status": filled(raw.get("status")),
        "merchant_category_code": filled(raw.get("merchant_category_code")),
        "bank_transaction_code": any(filled((raw.get("bank_transaction_code") or {}).get(k)) for k in ("code", "sub_code")) if isinstance(raw.get("bank_transaction_code"), dict) else False,
        "booking_date": filled(raw.get("booking_date")),
        "value_date": filled(raw.get("value_date")),
        "transaction_date": filled(raw.get("transaction_date")),
        "amount": filled((raw.get("transaction_amount") or {}).get("amount")) if isinstance(raw.get("transaction_amount"), dict) else False,
        "credit_debit_indicator": filled(raw.get("credit_debit_indicator")),
        "transaction_id": filled(raw.get("transaction_id")),
        "entry_reference": filled(raw.get("entry_reference")),
    }


def coverage(raws):
    counts = {name: 0 for name in field_presence({})}
    statuses = {}
    for raw in raws:
        for name, present in field_presence(raw).items():
            counts[name] += int(present)
        status = raw.get("status")
        if isinstance(status, str) and status.strip():
            statuses[status] = statuses.get(status, 0) + 1
    return {"total": len(raws), "fields": {name: {"present": count, "total": len(raws)}
                                             for name, count in counts.items()}, "statuses": statuses}
