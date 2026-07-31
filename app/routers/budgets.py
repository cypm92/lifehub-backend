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


def next_month_code(month_code: str) -> str:
    try:
        current = datetime.strptime(month_code, "%Y-%m")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="El mes debe tener el formato YYYY-MM") from error
    return f"{current.year + (current.month == 12):04d}-{1 if current.month == 12 else current.month + 1:02d}"


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


@weekly_router.post("/{month_code}/manual-cycle", status_code=201)
def create_manual_weekly_cycle(month_code: str, data: schemas.WeeklyManualCycleCreate,
                               db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    try:
        weekly_expense_service.create_manual_cycle(month_code, data.direction, current_user, db)
        return {"detail": "Semana añadida"}
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


@weekly_router.delete("/{month_code}/boundary-cycle", status_code=204)
def remove_boundary_weekly_cycle(month_code: str, direction: str,
                                 db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    if direction not in {"before", "after"}:
        raise HTTPException(status_code=422, detail="La dirección debe ser before o after")
    try:
        weekly_expense_service.remove_boundary_cycle(month_code, direction, current_user, db)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error


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

@router.get("/months")
def get_budget_months(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    budgets = (
        db.query(models.MonthlyBudget)
        .filter(models.MonthlyBudget.user_id == current_user.id, models.MonthlyBudget.is_created.is_(True))
        .order_by(models.MonthlyBudget.month_code)
        .all()
    )
    return [{"month_code": budget.month_code, "is_closed": budget.is_closed, "closed_at": budget.closed_at} for budget in budgets]


@router.post("/initialize", status_code=201)
def initialize_budget(data: schemas.BudgetInitializeRequest, db: Session = Depends(get_db),
                      current_user: models.User = Depends(security.get_current_user)):
    next_month_code(data.month_code)
    budget = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == current_user.id,
        models.MonthlyBudget.month_code == data.month_code,
    ).first()
    if budget:
        return {"month_code": budget.month_code}
    budget = models.MonthlyBudget(user_id=current_user.id, month_code=data.month_code, expected_income=0.0, is_created=True)
    db.add(budget)
    db.commit()
    weekly_expense_service.generate_monthly_weeks(data.month_code, current_user, db)
    return {"month_code": data.month_code}


@router.post("/{month_code}/close")
def close_budget(month_code: str, db: Session = Depends(get_db),
                 current_user: models.User = Depends(security.get_current_user)):
    budget = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == current_user.id,
        models.MonthlyBudget.month_code == month_code,
    ).first()
    if not budget:
        raise HTTPException(status_code=404, detail="El presupuesto no existe")
    budget.is_closed = True
    budget.closed_at = datetime.utcnow()
    db.commit()
    return {"month_code": month_code, "is_closed": True}


@router.post("/{month_code}/save-state", status_code=201)
def save_budget_state(month_code: str, db: Session = Depends(get_db),
                      current_user: models.User = Depends(security.get_current_user)):
    budget = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == current_user.id,
        models.MonthlyBudget.month_code == month_code,
    ).first()
    if not budget:
        raise HTTPException(status_code=404, detail="El presupuesto no existe")

    state = {
        "expected_income": budget.expected_income,
        "budget_items": [
            {"category_id": item.category_id, "planned_amount": item.planned_amount}
            for item in budget.items
        ],
        "fixed_expenses": [
            {
                "fixed_expense_id": entry.fixed_expense_id,
                "concept": entry.concept,
                "amount": entry.amount,
                "description": entry.description,
                "due_day": entry.due_day,
                "status": entry.status,
            }
            for entry in db.query(models.FixedExpenseMonth).join(models.FixedExpense).filter(
                models.FixedExpense.user_id == current_user.id,
                models.FixedExpenseMonth.month_code == month_code,
            )
        ],
        "weekly_expenses": [
            {"category": entry.category_name, "amount": entry.allocated_amount, "status": entry.status}
            for entry in db.query(models.WeeklyExpense).filter(
                models.WeeklyExpense.user_id == current_user.id,
                models.WeeklyExpense.month_code == month_code,
            )
        ],
        "extra_expenses": [
            {"concept": entry.concept, "amount": entry.amount, "status": entry.status}
            for entry in db.query(models.ExtraExpense).filter(
                models.ExtraExpense.user_id == current_user.id,
                models.ExtraExpense.month_code == month_code,
            )
        ],
    }
    db.add(models.MonthlyBudgetSnapshot(
        user_id=current_user.id,
        monthly_budget_id=budget.id,
        month_code=month_code,
        state=state,
    ))
    db.commit()
    return {"month_code": month_code, "saved_at": datetime.utcnow()}


@router.post("/{month_code}/create-next", status_code=201)
def create_next_budget(month_code: str, db: Session = Depends(get_db),
                       current_user: models.User = Depends(security.get_current_user)):
    source = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == current_user.id,
        models.MonthlyBudget.month_code == month_code,
    ).first()
    if not source:
        raise HTTPException(status_code=404, detail="El presupuesto de origen no existe")
    latest = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == current_user.id,
        models.MonthlyBudget.is_created.is_(True),
    ).order_by(models.MonthlyBudget.month_code.desc()).first()
    if latest and latest.id != source.id:
        raise HTTPException(status_code=400, detail="Solo puedes crear el mes posterior al último presupuesto")

    target_month = next_month_code(month_code)
    existing = db.query(models.MonthlyBudget).filter(
        models.MonthlyBudget.user_id == current_user.id,
        models.MonthlyBudget.month_code == target_month,
    ).first()
    if existing:
        if existing.is_created:
            return {"month_code": existing.month_code}
        target = existing
        target.expected_income = source.expected_income
        target.is_created = True
        db.query(models.BudgetItem).filter(models.BudgetItem.monthly_budget_id == target.id).delete()
        owned_fixed_expense_ids = db.query(models.FixedExpense.id).filter(
            models.FixedExpense.user_id == current_user.id
        )
        db.query(models.FixedExpenseMonth).filter(
            models.FixedExpenseMonth.fixed_expense_id.in_(owned_fixed_expense_ids),
            models.FixedExpenseMonth.month_code == target_month,
            models.FixedExpenseMonth.status != "Pagado",
        ).delete(synchronize_session=False)
        db.query(models.WeeklyExpense).filter(
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.month_code == target_month,
            models.WeeklyExpense.status != "Pagado",
        ).delete(synchronize_session=False)
        db.flush()
    else:
        target = models.MonthlyBudget(
            user_id=current_user.id,
            month_code=target_month,
            expected_income=source.expected_income,
            is_created=True,
        )
        db.add(target)
        db.flush()
    latest_snapshot = (
        db.query(models.MonthlyBudgetSnapshot)
        .filter(
            models.MonthlyBudgetSnapshot.user_id == current_user.id,
            models.MonthlyBudgetSnapshot.monthly_budget_id == source.id,
        )
        .order_by(models.MonthlyBudgetSnapshot.saved_at.desc())
        .first()
    )
    saved_state = latest_snapshot.state if latest_snapshot else {}
    saved_items = saved_state.get("budget_items", [])

    for item in saved_items or source.items:
        category_id = item["category_id"] if isinstance(item, dict) else item.category_id
        planned_amount = item["planned_amount"] if isinstance(item, dict) else item.planned_amount
        db.add(models.BudgetItem(
            monthly_budget_id=target.id,
            category_id=category_id,
            planned_amount=planned_amount,
        ))

    source_fixed_entries = (
        db.query(models.FixedExpenseMonth)
        .join(models.FixedExpense)
        .filter(
            models.FixedExpense.user_id == current_user.id,
            models.FixedExpense.is_active.is_(True),
            models.FixedExpenseMonth.month_code == month_code,
        )
        .all()
    )
    saved_fixed_expenses = saved_state.get("fixed_expenses", [])
    entries_to_copy = saved_fixed_expenses or source_fixed_entries
    active_fixed_ids = {
        fixed_expense.id
        for fixed_expense in db.query(models.FixedExpense).filter(
            models.FixedExpense.user_id == current_user.id,
            models.FixedExpense.is_active.is_(True),
        )
    }
    for entry in entries_to_copy:
        fixed_expense_id = entry["fixed_expense_id"] if isinstance(entry, dict) else entry.fixed_expense_id
        if fixed_expense_id not in active_fixed_ids:
            continue
        db.add(models.FixedExpenseMonth(
            fixed_expense_id=fixed_expense_id,
            month_code=target_month,
            concept=entry["concept"] if isinstance(entry, dict) else entry.concept,
            amount=entry["amount"] if isinstance(entry, dict) else entry.amount,
            description=entry.get("description") if isinstance(entry, dict) else entry.description,
            due_day=entry.get("due_day", 1) if isinstance(entry, dict) else entry.due_day,
            status="Esperando",
        ))
    db.commit()
    weekly_expense_service.generate_monthly_weeks(target_month, current_user, db)
    return {"month_code": target_month}

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
