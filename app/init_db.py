# backend/app/init_db.py
import os
from sqlalchemy.orm import Session
from app import models, security

# 💡 PON AQUÍ TUS NUEVAS CREDENCIALES:
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@lifehub.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "test123!")


def init_db(db: Session):
    """Crea el usuario administrador inicial si no existe."""
    admin_exists = db.query(models.User).filter(models.User.email == ADMIN_EMAIL).first()

    if not admin_exists:
        first_admin = models.User(
            name="Super Admin",
            email=ADMIN_EMAIL,
            hashed_password=security.get_password_hash(ADMIN_PASSWORD),
            role="admin"
        )
        db.add(first_admin)
        db.commit()
        print(f"🚀 [LifeHub] ¡Usuario Admin principal creado con éxito! ({ADMIN_EMAIL})")