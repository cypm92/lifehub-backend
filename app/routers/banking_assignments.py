"""API de asignaciones bancarias, exclusivamente informativas (fase 1)."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import banking_models as bm, models, security
from app.database import get_db
from app.services.banking_assignments import apply_rules, destinations, valid_destination

router = APIRouter(prefix="/banks", tags=["banks"])


class RuleInput(BaseModel):
    field: Literal["concept", "counterparty"]
    contains: str = Field(min_length=1, max_length=200)

    @field_validator("contains")
    @classmethod
    def normalize_contains(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("El texto de la regla no puede estar vacío")
        return value


class AssignmentInput(BaseModel):
    destination_type: Literal["fixed", "weekly", "extra", "income"]
    destination_id: int | None = Field(default=None, gt=0, strict=True)
    rule: RuleInput | None = None


def assignment_json(assignment):
    return {"id": assignment.id, "transaction_id": assignment.transaction_id,
            "destination_type": assignment.destination_type, "destination_id": assignment.destination_id}


def rule_json(rule):
    return {"id": rule.id, "field": rule.field, "contains": rule.contains,
            "destination_type": rule.destination_type, "destination_id": rule.destination_id}


def owned_transaction(db, user_id, transaction_id, lock=False):
    query = (db.query(bm.BankTransaction).join(bm.BankAccount).join(bm.BankConnection)
             .filter(bm.BankTransaction.id == transaction_id, bm.BankConnection.user_id == user_id))
    if lock:
        query = query.with_for_update(of=bm.BankTransaction)
    transaction = query.first()
    if transaction is None:
        raise HTTPException(404, "Movimiento no encontrado")
    return transaction


def require_destination(db, user_id, destination_type, destination_id):
    if not valid_destination(db, user_id, destination_type, destination_id):
        raise HTTPException(400, "Destino no válido o no pertenece al usuario")


def make_rule(db, user_id, assignment, payload):
    require_destination(db, user_id, assignment.destination_type, assignment.destination_id)
    rule = bm.BankRule(user_id=user_id, field=payload.field, contains=payload.contains,
                       destination_type=assignment.destination_type, destination_id=assignment.destination_id)
    db.add(rule)
    return rule


@router.get("/destinations")
def list_destinations(db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    return destinations(db, user.id)


@router.put("/transactions/{transaction_id}/assignment")
def assign(transaction_id: int, payload: AssignmentInput, db: Session = Depends(get_db),
           user: models.User = Depends(security.get_current_user)):
    transaction = owned_transaction(db, user.id, transaction_id, lock=True)
    require_destination(db, user.id, payload.destination_type, payload.destination_id)
    assignment = db.query(bm.BankAssignment).filter_by(transaction_id=transaction.id).first()
    if assignment is None:
        assignment = bm.BankAssignment(user_id=user.id, transaction_id=transaction.id)
        db.add(assignment)
    elif assignment.user_id != user.id:
        # Corrupt cross-user records must not be revealed or overwritten.
        raise HTTPException(409, "El movimiento ya tiene una asignación")
    assignment.destination_type = payload.destination_type
    assignment.destination_id = payload.destination_id
    rule = make_rule(db, user.id, assignment, payload.rule) if payload.rule else None
    db.flush()
    result = {"assignment": assignment_json(assignment), "rule": rule_json(rule) if rule else None}
    db.commit()
    return result


@router.delete("/transactions/{transaction_id}/assignment")
def unassign(transaction_id: int, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    transaction = owned_transaction(db, user.id, transaction_id, lock=True)
    assignment = db.query(bm.BankAssignment).filter_by(transaction_id=transaction.id, user_id=user.id).first()
    if assignment is None:
        raise HTTPException(404, "Asignación no encontrada")
    db.delete(assignment)
    db.commit()
    return {"deleted": True}


@router.post("/transactions/{transaction_id}/rules")
def create_rule(transaction_id: int, payload: RuleInput, db: Session = Depends(get_db),
                user: models.User = Depends(security.get_current_user)):
    transaction = owned_transaction(db, user.id, transaction_id, lock=True)
    assignment = db.query(bm.BankAssignment).filter_by(transaction_id=transaction.id, user_id=user.id).first()
    if assignment is None:
        raise HTTPException(400, "Asigne primero el movimiento")
    rule = make_rule(db, user.id, assignment, payload)
    db.flush()
    result = rule_json(rule)
    db.commit()
    return result


@router.get("/rules")
def list_rules(db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    return [rule_json(rule) for rule in db.query(bm.BankRule).filter_by(user_id=user.id).order_by(bm.BankRule.id).all()]


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    rule = db.query(bm.BankRule).filter_by(id=rule_id, user_id=user.id).first()
    if rule is None:
        raise HTTPException(404, "Regla no encontrada")
    db.delete(rule)
    db.commit()
    return {"deleted": True}


@router.post("/rules/apply")
def run_rules(db: Session = Depends(get_db), user: models.User = Depends(security.get_current_user)):
    result = apply_rules(db, user.id)
    db.commit()
    return result
