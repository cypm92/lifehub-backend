from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app import models, schemas, security
from app.database import get_db

router = APIRouter(prefix="/vehicles", tags=["Vehicles"])


def get_owned_vehicle(vehicle_id: int, user_id: int, db: Session) -> models.Vehicle:
    vehicle = db.query(models.Vehicle).filter(
        models.Vehicle.id == vehicle_id,
        models.Vehicle.user_id == user_id,
    ).first()
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehículo no encontrado")
    return vehicle


def reminder_status(vehicle: models.Vehicle, maintenance: models.VehicleMaintenance) -> str:
    today = date.today()
    warning_date = today + timedelta(days=30)
    if maintenance.next_km is not None and vehicle.current_km >= maintenance.next_km:
        return "Vencido"
    if maintenance.next_date and maintenance.next_date < today:
        return "Vencido"
    if maintenance.next_km is not None and vehicle.current_km >= maintenance.next_km - 1000:
        return "Próximo"
    if maintenance.next_date and maintenance.next_date <= warning_date:
        return "Próximo"
    return "Planificado"


@router.get("", response_model=list[schemas.VehicleResponse])
def list_vehicles(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    return db.query(models.Vehicle).filter(models.Vehicle.user_id == current_user.id).order_by(models.Vehicle.brand, models.Vehicle.model).all()


@router.post("", response_model=schemas.VehicleResponse, status_code=status.HTTP_201_CREATED)
def create_vehicle(payload: schemas.VehicleCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    duplicate = db.query(models.Vehicle).filter(
        models.Vehicle.user_id == current_user.id,
        models.Vehicle.license_plate == payload.license_plate.strip().upper(),
    ).first()
    if duplicate:
        raise HTTPException(status_code=400, detail="Ya existe un vehículo con esa matrícula")
    vehicle_data = payload.model_dump()
    vehicle_data["license_plate"] = payload.license_plate.strip().upper()
    vehicle = models.Vehicle(**vehicle_data, user_id=current_user.id)
    db.add(vehicle)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.patch("/{vehicle_id}", response_model=schemas.VehicleResponse)
def update_vehicle(vehicle_id: int, payload: schemas.VehicleUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    for key, value in payload.model_dump().items():
        setattr(vehicle, key, value.strip().upper() if key == "license_plate" else value)
    db.commit()
    db.refresh(vehicle)
    return vehicle


@router.delete("/{vehicle_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vehicle(vehicle_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    db.delete(vehicle)
    db.commit()


@router.get("/{vehicle_id}/dashboard")
def vehicle_dashboard(vehicle_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    expenses = db.query(models.VehicleExpense).filter(models.VehicleExpense.vehicle_id == vehicle.id).order_by(models.VehicleExpense.expense_date.desc()).all()
    maintenance = db.query(models.VehicleMaintenance).filter(models.VehicleMaintenance.vehicle_id == vehicle.id).order_by(models.VehicleMaintenance.next_date.asc().nullslast(), models.VehicleMaintenance.next_km.asc().nullslast()).all()
    due_items = [
        {"id": item.id, "title": item.title, "next_date": item.next_date, "next_km": item.next_km, "status": reminder_status(vehicle, item)}
        for item in maintenance if item.next_date or item.next_km is not None
    ]
    due_items.sort(key=lambda item: {"Vencido": 0, "Próximo": 1, "Planificado": 2}[item["status"]])
    return {
        "vehicle": vehicle,
        "expenses": expenses,
        "maintenance": maintenance,
        "total_cost": round(sum(item.amount for item in expenses) + sum(item.cost or 0 for item in maintenance), 2),
        "upcoming": due_items,
    }


@router.post("/{vehicle_id}/expenses", response_model=schemas.VehicleExpenseResponse, status_code=status.HTTP_201_CREATED)
def create_expense(vehicle_id: int, payload: schemas.VehicleExpenseCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    expense = models.VehicleExpense(vehicle_id=vehicle.id, **payload.model_dump())
    if expense.mileage and expense.mileage > vehicle.current_km:
        vehicle.current_km = expense.mileage
    db.add(expense)
    db.commit()
    db.refresh(expense)
    return expense


@router.patch("/{vehicle_id}/expenses/{expense_id}", response_model=schemas.VehicleExpenseResponse)
def update_expense(vehicle_id: int, expense_id: int, payload: schemas.VehicleExpenseUpdate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    expense = db.query(models.VehicleExpense).filter(
        models.VehicleExpense.id == expense_id,
        models.VehicleExpense.vehicle_id == vehicle.id,
    ).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    target_vehicle = vehicle
    if payload.vehicle_id and payload.vehicle_id != vehicle.id:
        target_vehicle = get_owned_vehicle(payload.vehicle_id, current_user.id, db)
        expense.vehicle_id = target_vehicle.id
    for key, value in payload.model_dump(exclude={"vehicle_id"}).items():
        setattr(expense, key, value)
    if expense.mileage and expense.mileage > target_vehicle.current_km:
        target_vehicle.current_km = expense.mileage
    db.commit()
    db.refresh(expense)
    return expense


@router.delete("/{vehicle_id}/expenses/{expense_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_expense(vehicle_id: int, expense_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    expense = db.query(models.VehicleExpense).filter(models.VehicleExpense.id == expense_id, models.VehicleExpense.vehicle_id == vehicle.id).first()
    if not expense:
        raise HTTPException(status_code=404, detail="Gasto no encontrado")
    db.delete(expense)
    db.commit()


@router.post("/{vehicle_id}/maintenance", response_model=schemas.VehicleMaintenanceResponse, status_code=status.HTTP_201_CREATED)
def create_maintenance(vehicle_id: int, payload: schemas.VehicleMaintenanceCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    record = models.VehicleMaintenance(vehicle_id=vehicle.id, **payload.model_dump())
    if record.completed_km and record.completed_km > vehicle.current_km:
        vehicle.current_km = record.completed_km
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.delete("/{vehicle_id}/maintenance/{maintenance_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_maintenance(vehicle_id: int, maintenance_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    vehicle = get_owned_vehicle(vehicle_id, current_user.id, db)
    record = db.query(models.VehicleMaintenance).filter(models.VehicleMaintenance.id == maintenance_id, models.VehicleMaintenance.vehicle_id == vehicle.id).first()
    if not record:
        raise HTTPException(status_code=404, detail="Mantenimiento no encontrado")
    db.delete(record)
    db.commit()
