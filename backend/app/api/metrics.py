from typing import Annotated

from fastapi import APIRouter, Header, Response

from app.config import settings as config
from app.services import metrics

router = APIRouter()


# AUTH: not the session cookie -- Prometheus cannot sign in. A bearer token from
# METRICS_TOKEN instead, and the endpoint does not exist until one is set. See SPEC 6.8.
@router.get("/metrics", include_in_schema=False)
def get_metrics(authorization: Annotated[str | None, Header()] = None) -> Response:
    metrics.check_scrape_token(config.metrics_token, authorization)
    return Response(content=metrics.generate_latest(), media_type=metrics.CONTENT_TYPE)
