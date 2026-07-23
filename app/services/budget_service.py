from sqlalchemy.orm import Session

from app import models, schemas
from app.services.weekly_expense_service import generate_monthly_weeks


def get_budget_summary(month_code: str, db: Session) -> dict:
    budget = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == month_code).first()
    if not budget:
        budget = models.MonthlyBudget(month_code=month_code, expected_income=0.0)
        db.add(budget)
        db.commit()
        db.refresh(budget)

    categories = db.query(models.Category).all()
    budget_items = {item.category_id: item.planned_amount for item in budget.items}
    transactions = db.query(models.Transaction).filter(models.Transaction.month_code == month_code).all()

    actual_spent_by_cat: dict[int, float] = {}
    actual_income = 0.0

    for transaction in transactions:
        if transaction.type == "ingreso":
            actual_income += transaction.amount
        elif transaction.category_id:
            actual_spent_by_cat[transaction.category_id] = (
                actual_spent_by_cat.get(transaction.category_id, 0.0) + transaction.amount
            )

    items_summary = []
    total_planned_expense = 0.0
    total_actual_expense = 0.0

    for category in categories:
        if category.type == "ingreso":
            continue

        planned = budget_items.get(category.id, 0.0)
        spent = actual_spent_by_cat.get(category.id, 0.0)
        pct = (spent / planned * 100) if planned > 0 else (100.0 if spent > 0 else 0.0)

        total_planned_expense += planned
        total_actual_expense += spent

        items_summary.append(
            {
                "category": category,
                "planned_amount": planned,
                "actual_spent": spent,
                "percentage_used": round(pct, 1),
            }
        )

    return {
        "month_code": month_code,
        "expected_income": budget.expected_income,
        "actual_income": actual_income,
        "total_planned_expense": total_planned_expense,
        "total_actual_expense": total_actual_expense,
        "items": items_summary,
    }


def set_expected_income(data: schemas.BudgetSetIncome, db: Session) -> None:
    budget = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == data.month_code).first()
    if not budget:
        budget = models.MonthlyBudget(month_code=data.month_code, expected_income=data.expected_income)
        db.add(budget)
    else:
        budget.expected_income = data.expected_income
    db.commit()


def set_budget_item(data: schemas.BudgetItemCreate, month_code: str, db: Session) -> None:
    budget = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == month_code).first()
    if not budget:
        budget = models.MonthlyBudget(month_code=month_code, expected_income=0.0)
        db.add(budget)
        db.commit()
        db.refresh(budget)

    item = (
        db.query(models.BudgetItem)
        .filter(
            models.BudgetItem.monthly_budget_id == budget.id,
            models.BudgetItem.category_id == data.category_id,
        )
        .first()
    )

    if item:
        item.planned_amount = data.planned_amount
    else:
        db.add(
            models.BudgetItem(
                monthly_budget_id=budget.id,
                category_id=data.category_id,
                planned_amount=data.planned_amount,
            )
        )

    db.commit()


def clone_budget(req: schemas.BudgetCloneRequest, db: Session) -> str:
    source = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == req.from_month).first()
    if not source:
        raise ValueError("El mes de origen no existe")

    target = db.query(models.MonthlyBudget).filter(models.MonthlyBudget.month_code == req.to_month).first()
    if not target:
        target = models.MonthlyBudget(month_code=req.to_month, expected_income=source.expected_income)
        db.add(target)
        db.commit()
        db.refresh(target)
    else:
        target.expected_income = source.expected_income

    for item in source.items:
        existing = (
            db.query(models.BudgetItem)
            .filter(
                models.BudgetItem.monthly_budget_id == target.id,
                models.BudgetItem.category_id == item.category_id,
            )
            .first()
        )
        if existing:
            existing.planned_amount = item.planned_amount
        else:
            db.add(
                models.BudgetItem(
                    monthly_budget_id=target.id,
                    category_id=item.category_id,
                    planned_amount=item.planned_amount,
                )
            )

    db.commit()
    generate_monthly_weeks(req.to_month, db)
    return f"Presupuesto clonado de {req.from_month} a {req.to_month}"
