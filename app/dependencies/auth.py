from typing import Callable, List

from fastapi import Depends, HTTPException, status

from app import models, security


def require_role(allowed_roles: List[str]) -> Callable:
    def role_checker(current_user: models.User = Depends(security.get_current_user)) -> models.User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permisos insuficientes",
            )
        return current_user

    return role_checker
