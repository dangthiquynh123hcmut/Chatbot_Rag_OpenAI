"""Custom exceptions raised by the services; main.py turns them into {"detail": message} responses."""

from __future__ import annotations

from fastapi import status


class AppException(Exception):
    """Base exception for the entire application."""

    status_code: int = status.HTTP_400_BAD_REQUEST

    def __init__(self, message: str, status_code: int | None = None, headers: dict[str, str] | None = None) -> None:
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.headers = headers
        super().__init__(message)


class BadRequestError(AppException):
    status_code = status.HTTP_400_BAD_REQUEST


class UnauthorizedError(AppException):
    status_code = status.HTTP_401_UNAUTHORIZED

    def __init__(self, message: str = "Chưa đăng nhập hoặc phiên đã hết hạn") -> None:
        super().__init__(message, headers={"WWW-Authenticate": "Bearer"})


class ForbiddenError(AppException):
    status_code = status.HTTP_403_FORBIDDEN


class NotFoundError(AppException):
    status_code = status.HTTP_404_NOT_FOUND
