"""Authentication dependencies of the routes."""

from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.containers import Container
from app.core.exceptions import ForbiddenError, UnauthorizedError
from app.schemas.auth_schema import AuthContext, UserResponse
from app.services.auth_service import AuthService

bearer = HTTPBearer(auto_error=False)


@inject
def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    auth_service: Annotated[AuthService, Depends(Provide[Container.auth_service_provider])],
) -> AuthContext:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise UnauthorizedError()
    return auth_service.authenticate(credentials.credentials)


def get_current_user(context: Annotated[AuthContext, Depends(get_auth_context)]) -> UserResponse:
    return context.user


def require_admin(user: Annotated[UserResponse, Depends(get_current_user)]) -> UserResponse:
    if user.role != "admin":
        raise ForbiddenError("Chỉ quản trị viên được thực hiện thao tác này")
    return user


CurrentContext = Annotated[AuthContext, Depends(get_auth_context)]
CurrentUser = Annotated[UserResponse, Depends(get_current_user)]
AdminUser = Annotated[UserResponse, Depends(require_admin)]
