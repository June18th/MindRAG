from fastapi import HTTPException


class AppException(HTTPException):
    def __init__(self, status_code: int, message: str, code: int | None = None):
        super().__init__(status_code=status_code, detail={"code": code or status_code, "message": message})


class RateLimitExceeded(AppException):
    def __init__(self, message: str, retry_after_seconds: int):
        super().__init__(status_code=429, message=message)
        self.retry_after_seconds = retry_after_seconds


class InvalidTokenException(AppException):
    def __init__(self, message: str = "Invalid or expired token"):
        super().__init__(status_code=401, message=message)
