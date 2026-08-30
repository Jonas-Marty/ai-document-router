class AppError(Exception):
    """Base for errors that map to the SPEC 5 error envelope.

    Services raise these; the handler in main.py turns them into
    `{"error": {"code", "message"}}`. Never raise HTTPException from a service.
    """

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


class OutsideAllowedRootsError(AppError):
    """A path resolved outside every permitted root. The security boundary said no."""

    code = "outside_allowed_roots"
    status_code = 403


class WebDAVUnreachable(AppError):
    """The WebDAV server could not be contacted."""

    code = "webdav_unreachable"
    status_code = 503


class WebDAVConflict(AppError):
    """The WebDAV server refused an operation because the target state conflicts."""

    code = "webdav_conflict"
    status_code = 409


class FilenameCollision(AppError):
    """Something already exists at the destination. The file was not touched.

    Distinct from WebDAVConflict because SPEC 7.1 makes a filename collision a *blocking*
    form state: the frontend keys the disabled approve button on this code.
    """

    code = "filename_collision"
    status_code = 409


class NotRevertible(AppError):
    """The file is no longer where history says it is, so it cannot be put back."""

    code = "not_revertible"
    status_code = 409


class AuthenticationRequired(AppError):
    """No valid session. The frontend turns this into "sign in", not an error toast."""

    code = "unauthenticated"
    status_code = 401


class InvalidCredentials(AppError):
    """Wrong email or wrong password -- deliberately indistinguishable to the caller."""

    code = "invalid_credentials"
    status_code = 401


class AdminRequired(AppError):
    """Signed in, but not as an admin. Distinct from 401: signing in again will not help."""

    code = "admin_required"
    status_code = 403


class RegistrationClosed(AppError):
    """Someone already claimed this instance and self-registration is off."""

    code = "registration_closed"
    status_code = 403


class OidcError(AppError):
    """The identity provider could not be used, or answered with something unusable."""

    code = "oidc_error"
    status_code = 502
