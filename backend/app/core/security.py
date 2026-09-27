import secrets

from fastapi import Depends, Header, HTTPException, status

from app.core.config import Settings, get_settings


def requerir_admin(
    x_admin_key: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Protección provisional de los endpoints de administración.

    Se reemplazará por usuarios con roles y MFA (ver docs/PLAN.md §8).
    """
    if not settings.admin_api_key:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Administración deshabilitada")
    if not x_admin_key or not secrets.compare_digest(x_admin_key, settings.admin_api_key):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Clave de administración inválida")
