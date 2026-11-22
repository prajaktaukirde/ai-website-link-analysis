import os
from secrets import compare_digest

import httpx
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Security
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse
from fastapi.security import APIKeyHeader

from .analysis import generate_report
from .extraction import UnsafeWebsite, WebsiteError, extract_website
from .schemas import LinkRequest, LinkResponse

load_dotenv()
app = FastAPI(title="AI-Powered Website Link Analysis", version="1.0.0")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse("/docs")


def analysis_access(key: str | None = Security(api_key_header)) -> None:
    expected = os.getenv("ANALYSIS_API_KEY")
    if expected and not compare_digest(expected.encode(), (key or "").encode()):
        raise HTTPException(401, "Invalid or missing X-API-Key")


@app.post("/link-analysis", response_model=LinkResponse, dependencies=[Depends(analysis_access)])
async def link_analysis(body: LinkRequest) -> LinkResponse:
    provider = os.getenv("LLM_PROVIDER", "gemini").strip().lower()
    if provider not in {"gemini", "openai"}:
        raise HTTPException(503, "Unsupported analysis provider")
    api_key = os.getenv(f"{provider.upper()}_API_KEY")
    if not api_key:
        raise HTTPException(503, "Analysis provider is not configured")
    url = str(body.url)
    try:
        pages = await run_in_threadpool(extract_website, url)
    except UnsafeWebsite as exc:
        raise HTTPException(422, str(exc)) from None
    except WebsiteError as exc:
        raise HTTPException(502, str(exc)) from None
    try:
        report = await generate_report(pages, api_key, provider)
    except httpx.TimeoutException:
        raise HTTPException(504, "Analysis provider timed out") from None
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        raise HTTPException(502, "Analysis provider returned an error or invalid analysis") from None
    return LinkResponse(website=url, sources=[page["url"] for page in pages], **report.model_dump())
