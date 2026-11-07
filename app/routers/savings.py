from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import get_db

router = APIRouter(prefix="/savings-sources", tags=["Savings"])


def owned(source_id: int, user_id: int, db: Session):
    source = (
        db.query(models.SavingsSource)
        .filter(models.SavingsSource.id == source_id, models.SavingsSource.user_id == user_id)
        .first()
    )
    if not source:
        raise HTTPException(status_code=404, detail="Fuente de ahorro no encontrada")
    return source


def serialize_source(source: models.SavingsSource, db: Session) -> dict:
    paid = 0.0
    if source.source_type == "Prestamo Familiar":
        paid = (
            db.query(func.coalesce(func.sum(models.FamilyLoanPayment.amount), 0.0))
            .filter(models.FamilyLoanPayment.savings_source_id == source.id)
            .scalar()
        )
    total = source.loan_total or 0.0
    return {
        "id": source.id,
        "name": source.name,
        "source_type": source.source_type,
        "balance": source.balance,
        "loan_direction": source.loan_direction,
        "loan_total": source.loan_total,
        "loan_paid": paid,
        "loan_remaining": max(total - paid, 0.0),
    }


def validate_loan(data: schemas.SavingsSourceCreate) -> None:
    if data.source_type != "Prestamo Familiar":
        return
    if data.loan_direction not in {"Recibido", "Concedido"}:
        raise HTTPException(status_code=422, detail="Indica si el préstamo es recibido o concedido")
    if data.loan_total is None or data.loan_total <= 0:
        raise HTTPException(status_code=422, detail="Indica el importe total del préstamo")


@router.get("", response_model=list[schemas.SavingsSourceResponse])
def get_sources(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    sources = (
        db.query(models.SavingsSource)
        .filter(models.SavingsSource.user_id == current_user.id)
        .order_by(models.SavingsSource.name)
        .all()
    )
    return [serialize_source(source, db) for source in sources]


@router.post("", response_model=schemas.SavingsSourceResponse, status_code=status.HTTP_201_CREATED)
def create_source(data: schemas.SavingsSourceCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    validate_loan(data)
    source = models.SavingsSource(user_id=current_user.id, **data.model_dump())
    db.add(source)
    db.commit()
    db.refresh(source)
    return serialize_source(source, db)


@router.put("/{source_id}", response_model=schemas.SavingsSourceResponse)
def update_source(source_id: int, data: schemas.SavingsSourceCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    validate_loan(data)
    source = owned(source_id, current_user.id, db)
    for field, value in data.model_dump().items():
        setattr(source, field, value)
    db.commit()
    db.refresh(source)
    return serialize_source(source, db)


@router.get("/{source_id}/payments", response_model=list[schemas.FamilyLoanPaymentResponse])
def get_loan_payments(source_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    source = owned(source_id, current_user.id, db)
    if source.source_type != "Prestamo Familiar":
        raise HTTPException(status_code=400, detail="Esta fuente no es un préstamo familiar")
    return (
        db.query(models.FamilyLoanPayment)
        .filter(models.FamilyLoanPayment.savings_source_id == source.id)
        .order_by(models.FamilyLoanPayment.payment_date.desc(), models.FamilyLoanPayment.id.desc())
        .all()
    )


@router.post("/{source_id}/payments", response_model=schemas.FamilyLoanPaymentResponse, status_code=status.HTTP_201_CREATED)
def add_loan_payment(source_id: int, data: schemas.FamilyLoanPaymentCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    source = owned(source_id, current_user.id, db)
    if source.source_type != "Prestamo Familiar":
        raise HTTPException(status_code=400, detail="Esta fuente no es un préstamo familiar")
    payment = models.FamilyLoanPayment(savings_source_id=source.id, **data.model_dump())
    db.add(payment)
    db.commit()
    db.refresh(payment)
    return payment


@router.delete("/{source_id}/payments/{payment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_loan_payment(source_id: int, payment_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    source = owned(source_id, current_user.id, db)
    payment = (
        db.query(models.FamilyLoanPayment)
        .filter(models.FamilyLoanPayment.id == payment_id, models.FamilyLoanPayment.savings_source_id == source.id)
        .first()
    )
    if not payment:
        raise HTTPException(status_code=404, detail="Movimiento no encontrado")
    db.delete(payment)
    db.commit()


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    db.delete(owned(source_id, current_user.id, db))
    db.commit()
