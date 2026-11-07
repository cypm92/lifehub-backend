from calendar import monthrange
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import get_db


router = APIRouter(prefix="/fixed-expenses", tags=["Fixed Expenses"])
groups_router = APIRouter(prefix="/fixed-expense-groups", tags=["Fixed Expense Groups"])


def parse_month(month_code: str) -> tuple[int, int]:
    try:
        parsed = datetime.strptime(month_code, "%Y-%m")
    except ValueError as error:
        raise HTTPException(status_code=422, detail="El mes debe tener el formato YYYY-MM") from error
    return parsed.year, parsed.month


def due_date(month_code: str, due_day: int) -> str:
    year, month = parse_month(month_code)
    return f"{year:04d}-{month:02d}-{min(due_day, monthrange(year, month)[1]):02d}"


def get_default_fixed_category(db: Session) -> models.Category | None:
    return db.query(models.Category).filter(models.Category.type == "gasto_fijo").first()


def ensure_month_entries(month_code: str, current_user: models.User, db: Session) -> None:
    parse_month(month_code)
    active_expenses = (
        db.query(models.FixedExpense)
        .filter(
            models.FixedExpense.user_id == current_user.id,
            models.FixedExpense.is_active.is_(True),
            models.FixedExpense.starts_month <= month_code,
        )
        .all()
    )
    existing_ids = {
        row.fixed_expense_id
        for row in db.query(models.FixedExpenseMonth.fixed_expense_id)
        .filter(models.FixedExpenseMonth.month_code == month_code)
        .all()
    }

    for expense in active_expenses:
        if expense.id not in existing_ids:
            db.add(
                models.FixedExpenseMonth(
                    fixed_expense_id=expense.id,
                    month_code=month_code,
                    concept=expense.concept,
                    amount=expense.amount,
                    description=expense.description,
                    due_day=expense.due_day,
                )
            )
    db.commit()


def serialize_month_entry(entry: models.FixedExpenseMonth) -> dict:
    expense = entry.fixed_expense
    group = expense.group
    installments_pending = None
    installments_percentage = None
    if expense.installments_total is not None:
        installments_pending = max(expense.installments_total - expense.installments_paid, 0)
        installments_percentage = round(expense.installments_paid / expense.installments_total * 100, 1)
    return {
        "id": entry.id,
        "fixed_expense_id": entry.fixed_expense_id,
        "month_code": entry.month_code,
        "concept": entry.concept,
        "amount": entry.amount,
        "description": entry.description or "",
        "due_day": entry.due_day,
        "due_date": due_date(entry.month_code, entry.due_day),
        "status": entry.status,
        "paid_at": entry.paid_at,
        "end_date": expense.end_date,
        "installments_total": expense.installments_total,
        "installments_paid": expense.installments_paid,
        "installments_pending": installments_pending,
        "installments_percentage": installments_percentage,
        "group_id": group.id if group else None,
        "group_name": group.name if group else None,
        "group_color": group.color if group else None,
    }


def get_owned_expense(expense_id: int, current_user: models.User, db: Session) -> models.FixedExpense:
    expense = (
        db.query(models.FixedExpense)
        .filter(models.FixedExpense.id == expense_id, models.FixedExpense.user_id == current_user.id)
        .first()
    )
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto fijo no encontrado")
    return expense


def get_owned_group(group_id: int, current_user: models.User, db: Session) -> models.FixedExpenseGroup:
    group = (
        db.query(models.FixedExpenseGroup)
        .filter(models.FixedExpenseGroup.id == group_id, models.FixedExpenseGroup.user_id == current_user.id)
        .first()
    )
    if not group:
        raise HTTPException(status_code=404, detail="Grupo de gasto fijo no encontrado")
    return group


@groups_router.get("", response_model=list[schemas.FixedExpenseGroupResponse])
def get_fixed_expense_groups(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    return (
        db.query(models.FixedExpenseGroup)
        .filter(models.FixedExpenseGroup.user_id == current_user.id)
        .order_by(models.FixedExpenseGroup.name)
        .all()
    )


@groups_router.post("", response_model=schemas.FixedExpenseGroupResponse, status_code=status.HTTP_201_CREATED)
def create_fixed_expense_group(
    data: schemas.FixedExpenseGroupCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    group = models.FixedExpenseGroup(user_id=current_user.id, **data.model_dump())
    db.add(group)
    try:
        db.commit()
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=400, detail="Ya existe un grupo con ese nombre") from error
    db.refresh(group)
    return group


@groups_router.patch("/{group_id}", response_model=schemas.FixedExpenseGroupResponse)
def update_fixed_expense_group(
    group_id: int,
    data: schemas.FixedExpenseGroupUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    group = get_owned_group(group_id, current_user, db)
    for field, value in data.model_dump().items():
        setattr(group, field, value)
    db.commit()
    db.refresh(group)
    return group


@groups_router.delete("/{group_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fixed_expense_group(
    group_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    group = get_owned_group(group_id, current_user, db)
    (
        db.query(models.FixedExpense)
        .filter(models.FixedExpense.group_id == group.id)
        .update({models.FixedExpense.group_id: None}, synchronize_session=False)
    )
    db.delete(group)
    db.commit()


@router.get("/{month_code}", response_model=list[schemas.FixedExpenseMonthResponse])
def get_fixed_expenses(
    month_code: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    parse_month(month_code)
    # Un mes puede haberse creado antes de añadir un gasto recurrente. Al abrir
    # su listado completamos exclusivamente las entradas que faltan para que
    # todos los gastos activos desde su mes de inicio estén representados.
    ensure_month_entries(month_code, current_user, db)
    entries = (
        db.query(models.FixedExpenseMonth)
        .join(models.FixedExpense)
        .filter(
            models.FixedExpense.user_id == current_user.id,
            models.FixedExpenseMonth.month_code == month_code,
            models.FixedExpense.starts_month <= month_code,
        )
        .order_by(models.FixedExpenseMonth.due_day, models.FixedExpenseMonth.id)
        .all()
    )
    return [serialize_month_entry(entry) for entry in entries]


@router.post("", response_model=schemas.FixedExpenseMonthResponse, status_code=status.HTTP_201_CREATED)
def create_fixed_expense(
    data: schemas.FixedExpenseCreate,
    month_code: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    parse_month(month_code)
    if data.installments_total is not None and data.installments_paid > data.installments_total:
        raise HTTPException(status_code=422, detail="Las cuotas pagadas no pueden superar el total")
    category_id = data.category_id
    if category_id is None:
        default_category = get_default_fixed_category(db)
        category_id = default_category.id if default_category else None

    if data.group_id is not None:
        get_owned_group(data.group_id, current_user, db)

    expense = models.FixedExpense(
        **data.model_dump(exclude={"category_id"}),
        category_id=category_id,
        user_id=current_user.id,
        starts_month=month_code,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)

    entry = models.FixedExpenseMonth(
        fixed_expense_id=expense.id,
        month_code=month_code,
        concept=expense.concept,
        amount=expense.amount,
        description=expense.description,
        due_day=expense.due_day,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return serialize_month_entry(entry)


@router.patch("/{expense_id}", response_model=schemas.FixedExpenseMonthResponse)
def update_fixed_expense(
    expense_id: int,
    data: schemas.FixedExpenseUpdate,
    month_code: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    expense = get_owned_expense(expense_id, current_user, db)
    changes = data.model_dump(exclude_unset=True)
    if "group_id" in changes and changes["group_id"] is not None:
        get_owned_group(changes["group_id"], current_user, db)
    total = changes.get("installments_total", expense.installments_total)
    paid = changes.get("installments_paid", expense.installments_paid)
    if total is not None and paid > total:
        raise HTTPException(status_code=422, detail="Las cuotas pagadas no pueden superar el total")
    for field, value in changes.items():
        setattr(expense, field, value)

    entry = (
        db.query(models.FixedExpenseMonth)
        .filter(
            models.FixedExpenseMonth.fixed_expense_id == expense.id,
            models.FixedExpenseMonth.month_code == month_code,
        )
        .first()
    )
    if entry and entry.status != "Pagado":
        for field in {"concept", "amount", "description", "due_day"}.intersection(changes):
            setattr(entry, field, getattr(expense, field))

    # Las ediciones de un gasto recurrente son la nueva configuración para los
    # próximos meses. Conservamos intactos los meses ya pagados para que el
    # histórico refleje lo que realmente ocurrió en cada uno de ellos.
    recurring_fields = {"concept", "amount", "description", "due_day"}.intersection(changes)
    if recurring_fields:
        future_entries = (
            db.query(models.FixedExpenseMonth)
            .filter(
                models.FixedExpenseMonth.fixed_expense_id == expense.id,
                models.FixedExpenseMonth.month_code > month_code,
                models.FixedExpenseMonth.status != "Pagado",
            )
            .all()
        )
        for future_entry in future_entries:
            for field in recurring_fields:
                setattr(future_entry, field, getattr(expense, field))

    db.commit()
    if not entry:
        ensure_month_entries(month_code, current_user, db)
        entry = (
            db.query(models.FixedExpenseMonth)
            .filter(
                models.FixedExpenseMonth.fixed_expense_id == expense.id,
                models.FixedExpenseMonth.month_code == month_code,
            )
            .first()
        )
    db.refresh(entry)
    return serialize_month_entry(entry)


@router.patch("/monthly/{entry_id}", response_model=schemas.FixedExpenseMonthResponse)
def update_monthly_status(
    entry_id: int,
    data: schemas.FixedExpenseMonthUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    entry = (
        db.query(models.FixedExpenseMonth)
        .join(models.FixedExpense)
        .filter(models.FixedExpenseMonth.id == entry_id, models.FixedExpense.user_id == current_user.id)
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Pago fijo no encontrado")

    expense = entry.fixed_expense
    if data.status == "Pagado" and entry.status != "Pagado" and not entry.counted_installment:
        if expense.installments_total is not None:
            expense.installments_paid = min(expense.installments_paid + 1, expense.installments_total)
            entry.counted_installment = True
    elif data.status != "Pagado" and entry.status == "Pagado" and entry.counted_installment:
        expense.installments_paid = max(expense.installments_paid - 1, 0)
        entry.counted_installment = False

    entry.status = data.status
    entry.paid_at = datetime.utcnow() if data.status == "Pagado" else None
    db.commit()
    db.refresh(entry)
    return serialize_month_entry(entry)


@router.delete("/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_fixed_expense(
    expense_id: int,
    month_code: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(security.get_current_user),
):
    expense = get_owned_expense(expense_id, current_user, db)
    expense.is_active = False
    (
        db.query(models.FixedExpenseMonth)
        .filter(
            models.FixedExpenseMonth.fixed_expense_id == expense.id,
            models.FixedExpenseMonth.month_code == month_code,
            models.FixedExpenseMonth.status != "Pagado",
        )
        .delete(synchronize_session=False)
    )
    db.commit()
