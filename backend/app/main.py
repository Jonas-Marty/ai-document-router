from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlmodel import Session
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import db
from app.api import documents as documents_api
from app.api import health
from app.api import settings as settings_api
from app.config import settings as config
from app.jobs import poller
from app.models import AppSettings
from app.services.errors import AppError

_STATUS_CODE_TO_ERROR_CODE = {404: "not_found"}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    with Session(db.engine) as session:
        if session.get(AppSettings, 1) is None:
            session.add(AppSettings(id=1))
            session.commit()
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
app.include_router(settings_api.router, prefix="/api/v1")
app.include_router(documents_api.router, prefix="/api/v1")
