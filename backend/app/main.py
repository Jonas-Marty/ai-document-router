import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db, log
from app.api import ai as ai_api
from app.api import audit as audit_api
from app.api import auth as auth_api
from app.api import configurations as configurations_api
from app.api import documents as documents_api
from app.api import folders as folders_api
from app.api import health
from app.api import history as history_api
from app.api import knowledge as knowledge_api
from app.api import metrics as metrics_api
from app.api import settings as settings_api
from app.config import settings as config
from app.jobs import poller
from app.services import metrics
from app.services.errors import AppError

_STATUS_CODE_TO_ERROR_CODE = {404: "not_found"}

# At import rather than in the lifespan: uvicorn's --reload worker and the container both
# import this module before anything logs, and the lifespan's own lines should be formatted.
log.configure_logging(config.log_level, config.log_format)
logger = logging.getLogger(__name__)
# A lambda rather than the engine itself, so a scrape reads whichever engine db holds now.
metrics.register_queue_collector(lambda: db.engine)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # No settings row to seed: each user's is created the first time they are asked for.
    poller.start_scheduler()
    try:
        yield
    finally:
        poller.stop_scheduler()


app = FastAPI(title="AI Document Router", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
    # Without this a cross-origin dev frontend cannot read the id it is meant to quote.
    expose_headers=[log.REQUEST_ID_HEADER],
)
# Added last, so it is the outermost middleware and its timing covers everything inside.
app.add_middleware(log.RequestLogMiddleware)


@app.exception_handler(Exception)
async def handle_unexpected_error(_request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback under the request's id, and hand the person that id to quote.

    Runs outside RequestLogMiddleware, so the id header is added here rather than there.
    """
    rid = log.request_id()
    logger.error("Unhandled error", exc_info=exc)
    reference = f" (reference {rid})" if rid else ""
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": f"Something went wrong on the server{reference}.",
            }
        },
        headers={log.REQUEST_ID_HEADER: rid} if rid else None,
    )


@app.exception_handler(AppError)
async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )


@app.exception_handler(RequestValidationError)
async def handle_request_validation_error(
    _request: Request, exc: RequestValidationError
) -> JSONResponse:
    message = "; ".join(
        f"{'.'.join(str(part) for part in error['loc'] if part != 'body')}: {error['msg']}"
        for error in exc.errors()
    )
    return JSONResponse(
        status_code=422,
        content={"error": {"code": "validation_error", "message": message}},
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_exception(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = _STATUS_CODE_TO_ERROR_CODE.get(exc.status_code, "http_error")
    message = exc.detail if isinstance(exc.detail, str) else "Request failed."
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": code, "message": message}},
    )


app.include_router(health.router, prefix="/api/v1")
app.include_router(auth_api.router, prefix="/api/v1")
app.include_router(settings_api.router, prefix="/api/v1")
app.include_router(ai_api.router, prefix="/api/v1")
app.include_router(configurations_api.router, prefix="/api/v1")
app.include_router(knowledge_api.router, prefix="/api/v1")
app.include_router(documents_api.router, prefix="/api/v1")
app.include_router(folders_api.router, prefix="/api/v1")
app.include_router(history_api.router, prefix="/api/v1")
app.include_router(audit_api.router, prefix="/api/v1")
app.include_router(metrics_api.router, prefix="/api/v1")
