from sqlalchemy.orm import Session

from app import models, security
from app.config import settings

DEFAULT_CATEGORIES = [
    {"name": "Vivienda / Alquiler", "icon": "🏠", "color": "#3b82f6", "type": "gasto_fijo"},
    {"name": "Suministros (Luz, Agua, Net)", "icon": "⚡", "color": "#f59e0b", "type": "gasto_fijo"},
    {"name": "Suscripciones / Servicios", "icon": "📺", "color": "#8b5cf6", "type": "gasto_fijo"},
    {"name": "Comida y Supermercado", "icon": "🛒", "color": "#10b981", "type": "gasto_semanal"},
    {"name": "Gasolina y Transporte", "icon": "🚗", "color": "#ef4444", "type": "gasto_semanal"},
    {"name": "Ocio y Salidas", "icon": "🍿", "color": "#ec4899", "type": "gasto_semanal"},
    {"name": "Imprevistos y Mantenimiento", "icon": "🛠️", "color": "#64748b", "type": "gasto_extra"},
    {"name": "Sueldo / Nómina", "icon": "💰", "color": "#22c55e", "type": "ingreso"},
]


def init_db(db: Session) -> None:
    admin_exists = db.query(models.User).filter(models.User.email == settings.admin_email).first()

    if not admin_exists:
        db.add(
            models.User(
                name="Super Admin",
                email=settings.admin_email,
                hashed_password=security.get_password_hash(settings.admin_password),
                role="admin",
            )
        )

    if db.query(models.Category).count() == 0:
        for cat in DEFAULT_CATEGORIES:
            db.add(models.Category(**cat))

    db.commit()
