from typing import Annotated

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials

from app.config import get_settings
from app.core.containers import Container
from app.routers.deps import CurrentContext, CurrentUser, bearer
from app.schemas.auth_schema import (
    AuthConfigResponse,
    ChangePasswordRequest,
    ChangePasswordResponse,
    Credentials,
    LoginResult,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.services.auth_service import AuthService

REFRESH_COOKIE = "rag_refresh"
REFRESH_COOKIE_PATH = "/api/auth"

router = APIRouter(prefix="/auth", tags=["auth"])

AuthServiceDep = Annotated[AuthService, Depends(Provide[Container.auth_service_provider])]


def _set_refresh_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        REFRESH_COOKIE,
        token,
        max_age=settings.refresh_token_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        path=REFRESH_COOKIE_PATH,
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        REFRESH_COOKIE,
        path=REFRESH_COOKIE_PATH,
        httponly=True,
        secure=get_settings().cookie_secure,
        samesite="strict",
    )


def _login_response(result: LoginResult, response: Response) -> TokenResponse:
    if result.refresh_token:
        _set_refresh_cookie(response, result.refresh_token)
    return result.tokens


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=TokenResponse)
@inject
def register(req: RegisterRequest, request: Request, response: Response, auth_service: AuthServiceDep):
    result = auth_service.register(req.username, req.password, req.display_name, request.headers.get("user-agent"))
    return _login_response(result, response)


@router.post("/login", response_model=TokenResponse)
@inject
def login(req: Credentials, request: Request, response: Response, auth_service: AuthServiceDep):
    result = auth_service.login(req.username, req.password, request.headers.get("user-agent"))
    return _login_response(result, response)


@router.post("/refresh", response_model=TokenResponse)
@inject
def refresh(request: Request, response: Response, auth_service: AuthServiceDep):
    result = auth_service.refresh(request.cookies.get(REFRESH_COOKIE))
    if result is None:
        # Returned, not raised: a revoked (replayed) session must still be committed
        expired = JSONResponse({"detail": "Phiên đăng nhập đã hết hạn"}, status.HTTP_401_UNAUTHORIZED)
        _clear_refresh_cookie(expired)
        return expired
    return _login_response(result, response)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
@inject
def logout(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    auth_service: AuthServiceDep,
):
    auth_service.logout(request.cookies.get(REFRESH_COOKIE), credentials.credentials if credentials else None)
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(done)
    return done


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
@inject
def logout_all(context: CurrentContext, auth_service: AuthServiceDep):
    auth_service.logout_all(context.user.id)
    done = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(done)
    return done


@router.post("/change-password", response_model=ChangePasswordResponse)
@inject
def change_password(req: ChangePasswordRequest, context: CurrentContext, auth_service: AuthServiceDep):
    return auth_service.change_password(context, req.current_password, req.new_password)


@router.get("/me", response_model=UserResponse)
def me(user: CurrentUser):
    return user


@router.get("/config", response_model=AuthConfigResponse)
@inject
def auth_config(auth_service: AuthServiceDep):
    return auth_service.auth_config()
