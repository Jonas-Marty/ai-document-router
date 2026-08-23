"""Contract tests: the *real* webdav4 client against canned Nextcloud responses.

tests/test_webdav.py uses a hand-written fake, which can only ever confirm the assumptions
baked into it. These tests drive an actual `webdav4.Client` over an httpx MockTransport, so
webdav4's own PROPFIND XML parsing produces the dicts our converter consumes. If webdav4
ever changes the shape it returns -- or if our reading of it is simply wrong -- these fail
where the fake would happily stay green.

Still no network: MockTransport answers every request locally.
"""

import httpx
import pytest
from webdav4.client import Client

from app.services.errors import NotFoundError, WebDAVConflict, WebDAVUnreachable
from app.services.webdav import WebDavService

BASE_URL = "https://cloud.example.com/remote.php/dav/files/jonas"
ROOTS = ["/Documents"]

MULTISTATUS_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns">
  <d:response>
    <d:href>/remote.php/dav/files/jonas/Documents/</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Fri, 21 Aug 2026 12:00:00 GMT</d:getlastmodified>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getetag>&quot;root-etag&quot;</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/jonas/Documents/invoice.pdf</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Fri, 21 Aug 2026 12:30:00 GMT</d:getlastmodified>
        <d:getcontentlength>54321</d:getcontentlength>
        <d:getcontenttype>application/pdf</d:getcontenttype>
        <d:resourcetype/>
        <d:getetag>&quot;file-etag&quot;</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
  <d:response>
    <d:href>/remote.php/dav/files/jonas/Documents/Finance/</d:href>
    <d:propstat>
      <d:prop>
        <d:getlastmodified>Fri, 21 Aug 2026 11:00:00 GMT</d:getlastmodified>
        <d:resourcetype><d:collection/></d:resourcetype>
        <d:getetag>&quot;dir-etag&quot;</d:getetag>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


def build_service(handler: object) -> WebDavService:
    http_client = httpx.Client(
        transport=httpx.MockTransport(handler),  # type: ignore[arg-type]
        auth=("jonas", "app-password"),
    )
    client = Client(base_url=BASE_URL, http_client=http_client)
    return WebDavService(client, ROOTS)


class TestRealListingParse:
    def test_parses_a_nextcloud_propfind_into_entries(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(207, text=MULTISTATUS_XML)

        entries = build_service(handler).list_dir("/Documents")

        # The listed collection itself is excluded; its children come through in order.
        assert [(e.path, e.name, e.is_dir) for e in entries] == [
            ("/Documents/invoice.pdf", "invoice.pdf", False),
            ("/Documents/Finance", "Finance", True),
        ]

    def test_file_metadata_survives_the_round_trip(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(207, text=MULTISTATUS_XML)

        entries = build_service(handler).list_dir("/Documents")
        invoice = next(e for e in entries if e.name == "invoice.pdf")

        assert invoice.size_bytes == 54321
        assert invoice.content_type == "application/pdf"
        assert invoice.modified is not None
        assert invoice.modified.year == 2026
        assert invoice.etag is not None

    def test_directories_report_no_size(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(207, text=MULTISTATUS_XML)

        entries = build_service(handler).list_dir("/Documents")
        finance = next(e for e in entries if e.name == "Finance")

        assert finance.is_dir is True
        assert finance.size_bytes is None

    def test_request_targets_the_right_url_under_the_base_path(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(207, text=MULTISTATUS_XML)

        build_service(handler).list_dir("/Documents")

        assert seen[0].method == "PROPFIND"
        assert str(seen[0].url).startswith(f"{BASE_URL}/Documents")


class TestRealErrorMapping:
    def test_404_becomes_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with pytest.raises(NotFoundError):
            build_service(handler).list_dir("/Documents")

    def test_connection_failure_becomes_unreachable(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        with pytest.raises(WebDAVUnreachable):
            build_service(handler).list_dir("/Documents")

    def test_412_on_move_becomes_a_conflict(self) -> None:
        """Overwrite: F plus an occupied destination is exactly the collision case."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(412)

        with pytest.raises(WebDAVConflict):
            build_service(handler).move("/Documents/a.pdf", "/Documents/b.pdf")

    def test_405_on_mkdir_becomes_a_conflict(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "MKCOL":
                return httpx.Response(405)
            return httpx.Response(404)

        with pytest.raises(WebDAVConflict):
            build_service(handler).mkdir("/Documents/Existing")


class TestRealMoveSemantics:
    def test_move_sends_overwrite_false_and_an_absolute_destination(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201)

        build_service(handler).move("/Documents/a.pdf", "/Documents/Filed/a.pdf")

        request = seen[0]
        assert request.method == "MOVE"
        # This header is what actually stops the server clobbering a file.
        assert request.headers["Overwrite"] == "F"
        assert request.headers["Destination"].endswith("/Documents/Filed/a.pdf")


FILE_PROPFIND_XML = """<?xml version="1.0"?>
<d:multistatus xmlns:d="DAV:">
  <d:response>
    <d:href>/remote.php/dav/files/jonas/Documents/a.pdf</d:href>
    <d:propstat>
      <d:prop>
        <d:getcontentlength>19</d:getcontentlength>
        <d:getcontenttype>application/pdf</d:getcontenttype>
        <d:resourcetype/>
      </d:prop>
      <d:status>HTTP/1.1 200 OK</d:status>
    </d:propstat>
  </d:response>
</d:multistatus>
"""


class TestRealStreaming:
    def test_read_stream_yields_the_body(self) -> None:
        """Note: webdav4's open() probes with a PROPFIND before issuing the GET, so a file
        read costs two round trips. The hand-written fake could not have shown this."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.method)
            if request.method == "PROPFIND":
                return httpx.Response(207, text=FILE_PROPFIND_XML)
            return httpx.Response(200, content=b"%PDF-1.7 body bytes")

        chunks = list(build_service(handler).read_stream("/Documents/a.pdf", chunk_size=8))

        assert b"".join(chunks) == b"%PDF-1.7 body bytes"
        assert seen == ["PROPFIND", "GET"]

    def test_read_stream_maps_a_missing_file_to_not_found(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        with pytest.raises(NotFoundError):
            list(build_service(handler).read_stream("/Documents/gone.pdf"))
