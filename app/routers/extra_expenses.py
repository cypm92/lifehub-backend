from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import get_db


router = APIRouter(prefix="/extra-expenses", tags=["Extra Expenses"])
categories_router = APIRouter(prefix="/extra-expense-categories", tags=["Extra Expense Categories"])


def validate_month(month_code: str) -> None:
    try:
        datetime.strptime(month_code, "%Y-%m")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="El mes debe tener el formato YYYY-MM") from error


def month_code_for_date(expense_date) -> str:
    return expense_date.strftime("%Y-%m")


def category_for_user(category_id: int, user_id: int, db: Session) -> models.ExtraExpenseCategory:
    category = db.query(models.ExtraExpenseCategory).filter(
        models.ExtraExpenseCategory.id == category_id,
        models.ExtraExpenseCategory.user_id == user_id,
    ).first()
    if not category:
        raise HTTPException(status_code=404, detail="Categoría de extra no encontrada")
    return category


def serialize(expense: models.ExtraExpense) -> dict:
    category = expense.category
    return {
        "id": expense.id, "month_code": expense.month_code, "concept": expense.concept,
        "description": expense.description or "",
        "amount": expense.amount, "expense_date": expense.expense_date,
        "status": expense.status,
        "category_id": category.id if category else None,
        "category_name": category.name if category else None,
        "category_color": category.color if category else None,
    }


@categories_router.get("", response_model=list[schemas.ExtraExpenseCategoryResponse])
def get_categories(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    return db.query(models.ExtraExpenseCategory).filter(
        models.ExtraExpenseCategory.user_id == current_user.id
    ).order_by(models.ExtraExpenseCategory.name).all()


@categories_router.post("", response_model=schemas.ExtraExpenseCategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(data: schemas.ExtraExpenseCategoryCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    category = models.ExtraExpenseCategory(user_id=current_user.id, **data.model_dump())
    db.add(category)
    try:
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una categoría con ese nombre") from error
    db.refresh(category)
    return category


@categories_router.patch("/{category_id}", response_model=schemas.ExtraExpenseCategoryResponse)
def update_category(category_id: int, data: schemas.ExtraExpenseCategoryUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    category = category_for_user(category_id, current_user.id, db)
    category.name, category.color = data.name, data.color
    db.commit(); db.refresh(category)
    return category


@categories_router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_category(category_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    category = category_for_user(category_id, current_user.id, db)
    db.query(models.ExtraExpense).filter(models.ExtraExpense.category_id == category.id).update(
        {models.ExtraExpense.category_id: None}, synchronize_session=False
    )
    db.delete(category); db.commit()


@router.get("/{month_code}", response_model=list[schemas.ExtraExpenseResponse])
def get_expenses(month_code: str, include_scheduled: bool = False, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    validate_month(month_code)
    month_filter = (
        or_(
            models.ExtraExpense.month_code == month_code,
            models.ExtraExpense.month_code > month_code,
        )
        if include_scheduled
        else models.ExtraExpense.month_code == month_code
    )
    rows = db.query(models.ExtraExpense).filter(
        month_filter,
        models.ExtraExpense.user_id == current_user.id,
    ).order_by(models.ExtraExpense.expense_date.asc(), models.ExtraExpense.id.asc()).all()
    return [serialize(row) for row in rows]


@router.post("", response_model=schemas.ExtraExpenseResponse, status_code=status.HTTP_201_CREATED)
def create_expense(data: schemas.ExtraExpenseCreate, month_code: str, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    validate_month(month_code)
    if data.category_id is not None:
        category_for_user(data.category_id, current_user.id, db)
    expense = models.ExtraExpense(
        user_id=current_user.id,
        month_code=month_code_for_date(data.expense_date),
        status="Pagado" if data.expense_date <= datetime.now().date() else "Pendiente",
        **data.model_dump(),
    )
    db.add(expense); db.commit(); db.refresh(expense)
    return serialize(expense)


@router.patch("/{expense_id}", response_model=schemas.ExtraExpenseResponse)
def update_expense(expense_id: int, data: schemas.ExtraExpenseUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    expense = db.query(models.ExtraExpense).filter(models.ExtraExpense.id == expense_id, models.ExtraExpense.user_id == current_user.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto extra no encontrado")
    if data.category_id is not None:
        category_for_user(data.category_id, current_user.id, db)
    changes = data.model_dump(exclude_none=True)
    if "expense_date" in changes and "status" not in changes:
        changes["status"] = "Pagado" if changes["expense_date"] <= datetime.now().date() else "Pendiente"
    for field, value in changes.items():
        setattr(expense, field, value)
    if "expense_date" in changes:
        expense.month_code = month_code_for_date(changes["expense_date"])
    db.commit(); db.refresh(expense)
    return serialize(expense)


@router.patch("/{expense_id}/status", response_model=schemas.ExtraExpenseResponse)
def update_expense_status(expense_id: int, data: schemas.ExtraExpenseStatusUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    expense = db.query(models.ExtraExpense).filter(models.ExtraExpense.id == expense_id, models.ExtraExpense.user_id == current_user.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto extra no encontrado")
    expense.status = data.status
    db.commit(); db.refresh(expense)
    return serialize(expense)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(expense_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    expense = db.query(models.ExtraExpense).filter(models.ExtraExpense.id == expense_id, models.ExtraExpense.user_id == current_user.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto extra no encontrado")
    db.delete(expense); db.commit()
