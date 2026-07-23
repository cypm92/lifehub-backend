from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app import models, schemas, security
from app.database import get_db

router = APIRouter(prefix="/savings-sources", tags=["Savings"])

def owned(source_id: int, user_id: int, db: Session):
    source = db.query(models.SavingsSource).filter(models.SavingsSource.id == source_id, models.SavingsSource.user_id == user_id).first()
    if not source: raise HTTPException(status_code=404, detail="Fuente de ahorro no encontrada")
    return source

@router.get("", response_model=list[schemas.SavingsSourceResponse])
def get_sources(db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    return db.query(models.SavingsSource).filter(models.SavingsSource.user_id == current_user.id).order_by(models.SavingsSource.name).all()

@router.post("", response_model=schemas.SavingsSourceResponse, status_code=status.HTTP_201_CREATED)
def create_source(data: schemas.SavingsSourceCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    source = models.SavingsSource(user_id=current_user.id, **data.model_dump()); db.add(source); db.commit(); db.refresh(source); return source

@router.put("/{source_id}", response_model=schemas.SavingsSourceResponse)
def update_source(source_id: int, data: schemas.SavingsSourceCreate, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    source = owned(source_id, current_user.id, db)
    for field, value in data.model_dump().items(): setattr(source, field, value)
    db.commit(); db.refresh(source); return source

@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: Session = Depends(get_db), current_user: models.User = Depends(security.get_current_user)):
    db.delete(owned(source_id, current_user.id, db)); db.commit()
