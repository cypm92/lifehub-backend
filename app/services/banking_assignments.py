"""Asignaciones informativas y reglas: no escriben en las tablas de presupuesto."""
from sqlalchemy.orm import Session

from app import banking_models as bm, models
from app.services.banking_sync import counterpart


DESTINATION_TYPES = ("fixed", "weekly", "extra", "income")
RULE_FIELDS = ("concept", "counterparty")


def valid_destination(db: Session, user_id: int, destination_type: str, destination_id: int | None) -> bool:
    if destination_type == "income":
        return destination_id is None
    if destination_id is None or destination_id <= 0:
        return False
    if destination_type == "fixed":
        return db.query(models.FixedExpenseMonth.id).join(models.FixedExpense).filter(
            models.FixedExpenseMonth.id == destination_id, models.FixedExpense.user_id == user_id).first() is not None
    model = {"weekly": models.WeeklyExpenseCategory, "extra": models.ExtraExpenseCategory}.get(destination_type)
    return model is not None and db.query(model.id).filter(model.id == destination_id, model.user_id == user_id).first() is not None


def destinations(db: Session, user_id: int):
    fixed = db.query(models.FixedExpenseMonth).join(models.FixedExpense).filter(
        models.FixedExpense.user_id == user_id).order_by(models.FixedExpenseMonth.month_code.desc(), models.FixedExpenseMonth.id).all()
    weekly = db.query(models.WeeklyExpenseCategory).filter_by(user_id=user_id).order_by(models.WeeklyExpenseCategory.name).all()
    extra = db.query(models.ExtraExpenseCategory).filter_by(user_id=user_id).order_by(models.ExtraExpenseCategory.name).all()
    return {
        "fixed": [{"id": item.id, "name": item.concept, "month_code": item.month_code} for item in fixed],
        "weekly": [{"id": item.id, "name": item.name} for item in weekly],
        "extra": [{"id": item.id, "name": item.name} for item in extra],
        "income": [{"id": None, "name": "Ingreso"}],
    }


def text_for_rule(raw, field):
    if field == "counterparty":
        value = counterpart(raw)
        return value if isinstance(value, str) else ""
    if field == "concept":
        remittance = raw.get("remittance_information")
        if isinstance(remittance, list):
            text = " ".join(v for v in remittance if isinstance(v, str))
        elif isinstance(remittance, str):
            text = remittance
        else:
            text = ""
        note = raw.get("note")
        return " ".join(part for part in (text, note if isinstance(note, str) else "") if part)
    return ""


def apply_rules(db: Session, user_id: int):
    """Primera regla válida por ID; solo movimientos sin asignación.

    Las filas del movimiento se bloquean también al asignar manualmente. La
    comprobación de asignación se repite tras el bloqueo para evitar carreras.
    """
    rules = db.query(bm.BankRule).filter_by(user_id=user_id).order_by(bm.BankRule.id).all()
    usable = [rule for rule in rules if rule.field in RULE_FIELDS and rule.contains.strip()
              and valid_destination(db, user_id, rule.destination_type, rule.destination_id)]
    stale = len(rules) - len(usable)
    if not usable:
        return {"assigned": 0, "invalid_rules": stale}
    assigned_exists = db.query(bm.BankAssignment.id).filter(
        bm.BankAssignment.transaction_id == bm.BankTransaction.id).exists()
    transactions = (db.query(bm.BankTransaction).join(bm.BankAccount).join(bm.BankConnection)
                    .filter(bm.BankConnection.user_id == user_id, ~assigned_exists)
                    .order_by(bm.BankTransaction.id).with_for_update(of=bm.BankTransaction).all())
    assigned = 0
    for tx in transactions:
        if db.query(bm.BankAssignment.id).filter_by(transaction_id=tx.id).first():
            continue
        for rule in usable:
            if rule.contains.strip().casefold() in text_for_rule(tx.raw_data, rule.field).casefold():
                db.add(bm.BankAssignment(user_id=user_id, transaction_id=tx.id,
                                         destination_type=rule.destination_type, destination_id=rule.destination_id))
                assigned += 1
                break
    return {"assigned": assigned, "invalid_rules": stale}
