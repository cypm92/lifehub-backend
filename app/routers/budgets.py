from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app import models, schemas, security
from app.database import get_db
from app.services import weekly_expense_service
import calendar
from datetime import datetime, timedelta

# 1. Router para presupuestos (colgará de /budgets gracias a main.py)
router = APIRouter(prefix="/budgets", tags=["Budgets"])

# 2. Router independiente para control semanal (colgará directamente de /weekly-expenses)
weekly_router = APIRouter(prefix="/weekly-expenses", tags=["Weekly Expenses"])
weekly_categories_router = APIRouter(prefix="/weekly-expense-categories", tags=["Weekly Expense Categories"])


# Reemplaza la función generate_monthly_weeks en tu budgets.py por esta:

def generate_monthly_weeks(month_code: str, db: Session):
    """Autogenera las semanas del mes utilizando únicamente las categorías semanales fijas."""
    existing = db.query(models.WeeklyExpense).filter(models.WeeklyExpense.month_code == month_code).first()
    if existing:
        return  # Ya existen, no duplicar

    parts = month_code.split("-")
    year, month = int(parts[0]), int(parts[1])

    # Categorías semanales fijas y exclusivas
    weekly_categories = ["Supermercado", "Gasolina", "Ocio"]

    current_date = datetime(year, month, 1)
    num_days = calendar.monthrange(year, month)[1]
    end_date = datetime(year, month, num_days)

    week_num = 1
    while current_date <= end_date:
        week_start = current_date
        days_to_sunday = (6 - week_start.weekday()) % 7
        week_end = min(week_start + timedelta(days=days_to_sunday), end_date)

        date_label = f"{week_start.day:02d}/{week_start.month:02d} - {week_end.day:02d}/{week_end.month:02d}"

        for cat_name in weekly_categories:
            new_expense = models.WeeklyExpense(
                month_code=month_code,
                week_number=week_num,
                week_date_label=date_label,
                category_name=cat_name,
                allocated_amount=0.0,
                status="Esperando"
            )
            db.add(new_expense)

        current_date = week_end + timedelta(days=1)
        week_num += 1

    db.commit()


# --- RUTAS DE CONTROL SEMANAL (/weekly-expenses/...) ---

@weekly_router.get("/{month_code}", response_model=List[schemas.WeeklyExpenseResponse])
def get_weekly_expenses(month_code: str, db: Session = Depends(get_db),
                        current_user: models.User = Depends(security.get_current_user)):
    try:
        return weekly_expense_service.get_weekly_expenses(month_code, current_user, db)
    except ValueError as error:
        raise HTTPException(status_code=422, detail="El mes debe tener el formato YYYY-MM") from error


@weekly_router.patch("/{expense_id}", response_model=schemas.WeeklyExpenseResponse)
def update_weekly_expense(expense_id: int, updates: dict, db: Session = Depends(get_db),
                          current_user: models.User = Depends(security.get_current_user)):
    try:
        return weekly_expense_service.update_weekly_expense(expense_id, updates, current_user, db)
    except ValueError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@weekly_categories_router.get("", response_model=List[schemas.WeeklyExpenseCategoryResponse])
def get_weekly_categories(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    return weekly_expense_service.get_weekly_categories(current_user, db)


@weekly_categories_router.post("", response_model=schemas.WeeklyExpenseCategoryResponse)
def create_weekly_category(data: schemas.WeeklyExpenseCategoryCreate, month_code: str,
                           db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    payload = data.model_dump()
    existing_categories = weekly_expense_service.get_weekly_categories(current_user, db)
    if existing_categories:
        payload["recharge_weekday"] = existing_categories[0].recharge_weekday
    category = models.WeeklyExpenseCategory(user_id=current_user.id, **payload)
    db.add(category)
    try:
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe una categoría con ese nombre") from error
    weekly_expense_service.generate_monthly_weeks(month_code, current_user, db)
    db.refresh(category)
    return category


@weekly_categories_router.patch("/{category_id}", response_model=schemas.WeeklyExpenseCategoryResponse)
def update_weekly_category(category_id: int, data: schemas.WeeklyExpenseCategoryUpdate, month_code: str,
                           db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    category = db.query(models.WeeklyExpenseCategory).filter(
        models.WeeklyExpenseCategory.id == category_id, models.WeeklyExpenseCategory.user_id == current_user.id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Categoría semanal no encontrada")
    changes = data.model_dump(exclude_unset=True)
    recharge_day_changed = "recharge_weekday" in changes and changes["recharge_weekday"] != category.recharge_weekday
    for field, value in changes.items():
        setattr(category, field, value)
    pending_expenses = db.query(models.WeeklyExpense).filter(
        models.WeeklyExpense.month_code == month_code,
        models.WeeklyExpense.user_id == current_user.id,
        models.WeeklyExpense.status != "Pagado",
    )
    if recharge_day_changed:
        db.query(models.WeeklyExpenseCategory).filter(
            models.WeeklyExpenseCategory.user_id == current_user.id,
            models.WeeklyExpenseCategory.is_active.is_(True),
        ).update({"recharge_weekday": category.recharge_weekday}, synchronize_session=False)
        pending_expenses.delete(synchronize_session=False)
    else:
        db.query(models.WeeklyExpense).filter(
            models.WeeklyExpense.weekly_category_id == category.id,
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.status != "Pagado",
        ).update({"category_name": category.name, "allocated_amount": category.weekly_amount}, synchronize_session=False)
    db.commit()
    if recharge_day_changed:
        weekly_expense_service.generate_monthly_weeks(month_code, current_user, db)
    db.refresh(category)
    return category


@weekly_categories_router.delete("/{category_id}", status_code=204)
def delete_weekly_category(category_id: int, month_code: str,
                           db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    category = db.query(models.WeeklyExpenseCategory).filter(
        models.WeeklyExpenseCategory.id == category_id, models.WeeklyExpenseCategory.user_id == current_user.id).first()
    if not category:
        raise HTTPException(status_code=404, detail="Categoría semanal no encontrada")
    category.is_active = False
    db.query(models.WeeklyExpense).filter(
        models.WeeklyExpense.weekly_category_id == category.id,
        models.WeeklyExpense.month_code == month_code,
        models.WeeklyExpense.user_id == current_user.id,
        models.WeeklyExpense.status != "Pagado",
    ).delete(synchronize_session=False)
    db.commit()


# --- RUTAS DE PRESUPUESTOS (/budgets/...) ---

@router.post("/clone")
def clone_budget(req: schemas.BudgetCloneRequest, db: Session = Depends(get_db),
                 current_user: models.User = Depends(security.get_current_user)):
    """Clona los límites planeados de un mes a otro e inicializa las semanas"""
    
    # 1. Clonar Presupuesto Mensual
    source = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == req.from_month).first()
    if not source:
        raise HTTPException(status_code=404, detail="El mes de origen no existe")

    target = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == req.to_month).first()
    if not target:
        target = models.MonthlyBudget(month_code=req.to_month, expected_income=source.expected_income)
        db.add(target)
        db.commit()
        db.refresh(target)
    else:
        target.expected_income = source.expected_income

    # Copiar elementos (categorías planeadas)
    for item in source.items:
        existing = db.query(models.BudgetItem).filter(
            models.BudgetItem.monthly_budget_id == target.id,
            models.BudgetItem.category_id == item.category_id
        ).first()
        if existing:
            existing.planned_amount = item.planned_amount
        else:
            db.add(models.BudgetItem(
                monthly_budget_id=target.id,
                category_id=item.category_id,
                planned_amount=item.planned_amount
            ))

    # 2. Inicializar Semanas (lógica previa del router)
    weekly_expense_service.generate_monthly_weeks(req.to_month, current_user, db)

    db.commit()
    return {"message": f"Presupuesto y semanas clonados de {req.from_month} a {req.to_month}"}
