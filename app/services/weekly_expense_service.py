import calendar
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app import models


def parse_month(month_code: str) -> tuple[int, int]:
    parsed = datetime.strptime(month_code, "%Y-%m")
    return parsed.year, parsed.month


def get_weekly_categories(current_user: models.User, db: Session) -> list[models.WeeklyExpenseCategory]:
    return (
        db.query(models.WeeklyExpenseCategory)
        .filter(
            models.WeeklyExpenseCategory.user_id == current_user.id,
            models.WeeklyExpenseCategory.is_active.is_(True),
        )
        .order_by(models.WeeklyExpenseCategory.id)
        .all()
    )


def generate_monthly_weeks(month_code: str, current_user: models.User, db: Session) -> None:
    """Genera las semanas que falten para las categorías configuradas por el usuario."""
    year, month = parse_month(month_code)
    categories = get_weekly_categories(current_user, db)
    if not categories:
        return

    existing_keys = {
        (expense.week_number, expense.weekly_category_id)
        for expense in (
            db.query(models.WeeklyExpense)
            .filter(
                models.WeeklyExpense.month_code == month_code,
                models.WeeklyExpense.user_id == current_user.id,
            )
            .all()
        )
    }

    current_date = datetime(year, month, 1)
    end_date = datetime(year, month, calendar.monthrange(year, month)[1])
    week_number = 1
    while current_date <= end_date:
        week_end = min(current_date + timedelta(days=(6 - current_date.weekday()) % 7), end_date)
        label = f"{current_date.day:02d}/{current_date.month:02d} - {week_end.day:02d}/{week_end.month:02d}"
        for category in categories:
            key = (week_number, category.id)
            if key not in existing_keys:
                db.add(models.WeeklyExpense(
                    user_id=current_user.id,
                    weekly_category_id=category.id,
                    month_code=month_code,
                    week_number=week_number,
                    week_date_label=label,
                    category_name=category.name,
                    allocated_amount=category.weekly_amount,
                    status="Esperando",
                ))
        current_date = week_end + timedelta(days=1)
        week_number += 1
    db.commit()


def get_weekly_expenses(month_code: str, current_user: models.User, db: Session) -> list[models.WeeklyExpense]:
    generate_monthly_weeks(month_code, current_user, db)
    return (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
        )
        .order_by(models.WeeklyExpense.week_number, models.WeeklyExpense.id)
        .all()
    )


def update_weekly_expense(expense_id: int, updates: dict, current_user: models.User, db: Session) -> models.WeeklyExpense:
    expense = (
        db.query(models.WeeklyExpense)
        .filter(models.WeeklyExpense.id == expense_id, models.WeeklyExpense.user_id == current_user.id)
        .first()
    )
    if not expense:
        raise ValueError("Gasto semanal no encontrado")

    for key in {"allocated_amount", "status", "week_date_label"}.intersection(updates):
        setattr(expense, key, updates[key])
    db.commit()
    db.refresh(expense)
    return expense
