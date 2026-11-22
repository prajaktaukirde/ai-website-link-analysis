import os
import socket
import unittest
from io import BytesIO
from pathlib import Path
from time import monotonic
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from app.extraction import UnsafeWebsite, extract_website, fetch_page, public_ip
from app.main import app
from app.schemas import LinkResponse

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "link-analysis-response.json"


class APITests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        environment = patch.dict(os.environ, {
            "LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test", "ANALYSIS_API_KEY": "analysis-test",
        })
        environment.start()
        self.addCleanup(environment.stop)

    def analysis(self, url):
        return self.client.post("/link-analysis", json={"url": url}, headers={"X-API-Key": "analysis-test"})

    def test_home_redirects_to_docs(self):
        response = self.client.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertEqual(response.headers["location"], "/docs")

    def test_private_dns_is_rejected_before_connecting(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443))]
        with patch("app.extraction.socket.getaddrinfo", return_value=records), patch(
            "app.extraction.socket.socket"
        ) as connect:
            with self.assertRaises(UnsafeWebsite):
                extract_website("https://example.com/")
            connect.assert_not_called()

    def test_analysis_validation(self):
        for url in ("file:///etc/passwd", "https://user:pass@example.com", "https://example.com:8080"):
            with self.subTest(url=url):
                self.assertEqual(self.analysis(url).status_code, 422)

    def test_public_ip_check(self):
        self.assertTrue(public_ip("93.184.216.34"))
        self.assertTrue(public_ip("2606:4700:4700::1111"))
        for address in ("127.0.0.1", "169.254.169.254", "::1", "::ffff:10.0.0.1", "64:ff9b::a00:1"):
            with self.subTest(address=address):
                self.assertFalse(public_ip(address))

    def test_connection_close_keeps_transport_open_for_body(self):
        records = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
        transport = MagicMock()
        transport.__enter__.return_value = transport
        transport.makefile.return_value = BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\n"
            b"Content-Length: 4\r\nConnection: close\r\n\r\nTest"
        )

        def check_timeout(_seconds):
            self.assertFalse(transport.close.called)

        transport.settimeout.side_effect = check_timeout
        with patch("app.extraction.socket.getaddrinfo", return_value=records), patch(
            "app.extraction.socket.socket", return_value=transport
        ):
            _, body, _ = fetch_page("http://example.com/", monotonic() + 5)
        self.assertEqual(body, b"Test")

    def test_extracts_related_page_and_drops_boilerplate(self):
        home = "https://example.com/"
        services = home + "services"
        html = {
            home: b'<title>Workshop</title><nav><a href="/services">Services</a></nav>'
                  b'<main><script>Ignore this</script><h1>Bicycle Repair</h1><p>Bicycle Repair</p></main>',
            services: b"<main><h1>Bicycle tune-ups</h1><p>Puncture repair</p></main>",
        }
        with patch("app.extraction.fetch_page", side_effect=lambda url, *_: (url, html[url], "utf-8")):
            pages = extract_website(home)
        self.assertEqual([page["url"] for page in pages], [home, services])
        self.assertEqual(pages[0]["text"], "Bicycle Repair")
        self.assertIn("Puncture repair", pages[1]["text"])

    def test_model_timeout_is_reported(self):
        with patch("app.main.extract_website", return_value=[]), patch(
            "app.main.generate_report", side_effect=httpx.ReadTimeout("timeout")
        ):
            self.assertEqual(self.analysis("https://example.com").status_code, 504)

    def test_analysis_requires_key_when_configured(self):
        response = self.client.post("/link-analysis", json={"url": "https://example.com"})
        self.assertEqual(response.status_code, 401)

    def test_analysis_request_to_structured_response(self):
        pages = [{"url": "https://example.com/", "text": "Workshop in Pune. Bicycle tune-ups. Puncture repair."}]
        expected = LinkResponse.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))
        model_response = {"choices": [{"finish_reason": "stop", "message": {
            "content": expected.model_dump_json(exclude={"success", "website", "sources"})
        }}]}
        request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
        model_post = AsyncMock(return_value=httpx.Response(200, json=model_response, request=request))
        with patch("app.main.extract_website", return_value=pages), patch(
            "app.analysis.httpx.AsyncClient.post", model_post
        ):
            response = self.analysis("https://example.com")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected.model_dump())
        payload = model_post.call_args.kwargs["json"]
        self.assertIn("Puncture repair", payload["messages"][1]["content"])
        self.assertTrue(payload["response_format"]["json_schema"]["strict"])

    def test_gemini_request_to_structured_response(self):
        pages = [{"url": "https://example.com/", "text": "Bicycle tune-ups"}]
        expected = LinkResponse.model_validate_json(EXAMPLE.read_text(encoding="utf-8"))
        model_response = {"candidates": [{"finishReason": "STOP", "content": {"parts": [{
            "text": expected.model_dump_json(exclude={"success", "website", "sources"}),
        }]}}]}
        model_post = AsyncMock(return_value=httpx.Response(
            200, json=model_response, request=httpx.Request("POST", "https://generativelanguage.googleapis.com")
        ))
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "test"}), patch(
            "app.main.extract_website", return_value=pages
        ), patch("app.analysis.httpx.AsyncClient.post", model_post):
            response = self.analysis("https://example.com")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), expected.model_dump())
        self.assertEqual(model_post.call_args.kwargs["headers"], {"x-goog-api-key": "test"})
        payload = model_post.call_args.kwargs["json"]
        self.assertIn("Bicycle tune-ups", payload["contents"][0]["parts"][0]["text"])
        self.assertIn("responseJsonSchema", payload["generationConfig"])
