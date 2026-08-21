class AppError(Exception):
    code: str = "internal_error"
    status_code: int = 400

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class NotFoundError(AppError):
    code = "not_found"
    status_code = 404


class ValidationError(AppError):
    code = "validation_error"
    status_code = 422
