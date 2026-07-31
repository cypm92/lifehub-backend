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


def format_period_label(start_date, end_date) -> str:
    weekday_names = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
    month_names = [
        "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
        "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre",
    ]
    return (
        f"{weekday_names[start_date.weekday()]} {start_date.day} de {month_names[start_date.month - 1]}"
        f" - {weekday_names[end_date.weekday()]} {end_date.day} de {month_names[end_date.month - 1]}"
    )


def refresh_cycle_metadata(month_code: str, current_user: models.User, db: Session) -> None:
    """Recalcula el orden y los rangos de todos los ciclos, incluidos los manuales."""
    expenses = (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.recharge_date.is_not(None),
            models.WeeklyExpense.is_excluded_cycle.is_(False),
        )
        .all()
    )
    dates = sorted({expense.recharge_date for expense in expenses})
    for index, recharge_date in enumerate(dates):
        cycle_expenses = [expense for expense in expenses if expense.recharge_date == recharge_date]
        if index + 1 < len(dates):
            period_end = dates[index + 1] - timedelta(days=1)
        else:
            # Un período semanal siempre dura siete días completos. El último
            # puede terminar en el mes siguiente (por ejemplo, vie. 28/08 - jue. 03/09).
            period_end = recharge_date + timedelta(days=6)
        for expense in cycle_expenses:
            expense.week_number = index + 1
            expense.week_date_label = format_period_label(recharge_date, period_end)


def generate_monthly_weeks(month_code: str, current_user: models.User, db: Session) -> None:
    """Create one budget cycle for every configured recharge day in the month."""
    year, month = parse_month(month_code)
    categories = get_weekly_categories(current_user, db)
    if not categories:
        return

    days_in_month = calendar.monthrange(year, month)[1]
    # The recharge day is shared by every weekly category. Existing accounts that
    # predate this rule use the first configured category as their common setting.
    recharge_weekday = categories[0].recharge_weekday
    recharge_dates = [
        datetime(year, month, day).date()
        for day in range(1, days_in_month + 1)
        if datetime(year, month, day).weekday() == recharge_weekday
    ]
    cycle_number = {recharge_date: index + 1 for index, recharge_date in enumerate(recharge_dates)}
    period_labels = {}
    for index, recharge_date in enumerate(recharge_dates):
        period_end = (
            recharge_dates[index + 1] - timedelta(days=1)
            if index + 1 < len(recharge_dates)
            else datetime(year, month, days_in_month).date()
        )
        period_labels[recharge_date] = format_period_label(recharge_date, period_end)

    # Migrate confirmed legacy records to their matching recharge date. Pending
    # legacy cycles are safely replaced because they used the old partial-week rule.
    legacy_expenses = (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.recharge_date.is_(None),
        )
        .order_by(models.WeeklyExpense.weekly_category_id, models.WeeklyExpense.week_number, models.WeeklyExpense.id)
        .all()
    )
    category_dates = {category.id: recharge_dates for category in categories}
    paid_positions: dict[int, int] = {}
    for expense in legacy_expenses:
        if expense.status != "Pagado":
            db.delete(expense)
            continue
        position = paid_positions.get(expense.weekly_category_id, 0)
        dates = category_dates.get(expense.weekly_category_id, [])
        if position >= len(dates):
            continue
        recharge_date = dates[position]
        expense.recharge_date = recharge_date
        expense.week_number = cycle_number[recharge_date]
        expense.week_date_label = period_labels[recharge_date]
        paid_positions[expense.weekly_category_id] = position + 1
    db.flush()

    existing_keys = {
        (expense.weekly_category_id, expense.recharge_date)
        for expense in (
            db.query(models.WeeklyExpense)
            .filter(
                models.WeeklyExpense.month_code == month_code,
                models.WeeklyExpense.user_id == current_user.id,
            )
            .all()
        )
        if expense.recharge_date is not None
    }

    # Keep labels and order aligned when the schedule format changes, including
    # records that were already marked as paid.
    for expense in (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.recharge_date.in_(recharge_dates),
        )
        .all()
    ):
        expense.week_number = cycle_number[expense.recharge_date]
        expense.week_date_label = period_labels[expense.recharge_date]

    for category in categories:
        for recharge_date in recharge_dates:
            if (category.id, recharge_date) in existing_keys:
                continue
            db.add(models.WeeklyExpense(
                user_id=current_user.id,
                weekly_category_id=category.id,
                month_code=month_code,
                week_number=cycle_number[recharge_date],
                recharge_date=recharge_date,
                week_date_label=period_labels[recharge_date],
                category_name=category.name,
                allocated_amount=category.weekly_amount,
                status="Esperando",
            ))
    db.flush()
    refresh_cycle_metadata(month_code, current_user, db)
    db.commit()


def create_manual_cycle(month_code: str, direction: str, current_user: models.User, db: Session) -> None:
    generate_monthly_weeks(month_code, current_user, db)
    categories = get_weekly_categories(current_user, db)
    expenses = (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.recharge_date.is_not(None),
            models.WeeklyExpense.is_excluded_cycle.is_(False),
        )
        .all()
    )
    if not categories or not expenses:
        raise ValueError("Configura al menos una categoría semanal antes de añadir una semana")

    boundary_date = min(expense.recharge_date for expense in expenses) if direction == "before" else max(expense.recharge_date for expense in expenses)
    recharge_date = boundary_date - timedelta(days=7) if direction == "before" else boundary_date + timedelta(days=7)
    if any(expense.recharge_date == recharge_date for expense in expenses):
        raise ValueError("Esa semana ya existe")

    for category in categories:
        db.add(models.WeeklyExpense(
            user_id=current_user.id,
            weekly_category_id=category.id,
            month_code=month_code,
            week_number=0,
            recharge_date=recharge_date,
            week_date_label="",
            category_name=category.name,
            allocated_amount=category.weekly_amount,
            status="Esperando",
            is_manual_cycle=True,
        ))
    db.flush()
    refresh_cycle_metadata(month_code, current_user, db)
    db.commit()


def remove_boundary_cycle(month_code: str, direction: str, current_user: models.User, db: Session) -> None:
    """Oculta el primer o último ciclo sin permitir que se regenere automáticamente."""
    generate_monthly_weeks(month_code, current_user, db)
    expenses = (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.recharge_date.is_not(None),
            models.WeeklyExpense.is_excluded_cycle.is_(False),
        )
        .all()
    )
    cycle_dates = sorted({expense.recharge_date for expense in expenses})
    if len(cycle_dates) <= 1:
        raise ValueError("Debe quedar al menos una semana en el mes")

    boundary_date = cycle_dates[0] if direction == "before" else cycle_dates[-1]
    for expense in expenses:
        if expense.recharge_date == boundary_date:
            expense.is_excluded_cycle = True

    db.flush()
    refresh_cycle_metadata(month_code, current_user, db)
    db.commit()


def get_weekly_expenses(month_code: str, current_user: models.User, db: Session) -> list[models.WeeklyExpense]:
    generate_monthly_weeks(month_code, current_user, db)
    return (
        db.query(models.WeeklyExpense)
        .filter(
            models.WeeklyExpense.month_code == month_code,
            models.WeeklyExpense.user_id == current_user.id,
            models.WeeklyExpense.is_excluded_cycle.is_(False),
        )
        .order_by(models.WeeklyExpense.recharge_date, models.WeeklyExpense.week_number, models.WeeklyExpense.id)
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
