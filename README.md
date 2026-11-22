# Website Link Analysis API

FastAPI endpoint that turns a website URL into a structured competitor, search query, SEO, service, and audience report.

[Live demo](https://ai-website-link-analysis.vercel.app/) · [Swagger docs](https://ai-website-link-analysis.vercel.app/docs) · [Sample JSON response](examples/link-analysis-response.json)

## Try it live

In Swagger, click **Authorize** and enter the analysis access key supplied separately. Open **POST /link-analysis → Try it out**, paste this request, and click **Execute**.

```json
{"url": "https://www.mozilla.org/en-US/"}
```

**Verified result:** HTTP 200 for Mozilla with all five report sections, three competitor suggestions, five search queries, and five service entries. [Verification log](examples/verification.txt)

## Run locally

Python 3.12+ · PowerShell

```powershell
python -m venv .venv
Copy-Item .env.example .env
.\.venv\Scripts\python -m pip install -r requirements.txt
```

Set `GEMINI_API_KEY` in `.env`. For OpenAI, set `LLM_PROVIDER=openai` and `OPENAI_API_KEY`. Set `ANALYSIS_API_KEY` to require the `X-API-Key` header on a public deployment.

```powershell
.\.venv\Scripts\python -m uvicorn app.main:app --host 0.0.0.0 --port 4000
.\.venv\Scripts\python -m unittest discover -s tests -v
```

[Local Swagger](http://localhost:4000/docs)

## How it works

The API validates URLs and blocks private network destinations, including redirects. It extracts and deduplicates text from up to three pages, then makes one LLM call and validates the JSON with Pydantic. The report labels inferred services and AI-generated competitor suggestions. Search queries cover Google, Facebook, Instagram, LinkedIn, and X.

## Model comparison and recommendation

Standard USD pricing per million text tokens. Speeds and task fit are estimates. No comparative accuracy benchmark was run.

| Model | Input / output | Context | JSON support | Estimated model time and fit |
| --- | ---: | ---: | --- | --- |
| [Gemini 3.1 Flash-Lite](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.1-flash-lite) | $0.25 / $1.50 | 1M input | JSON Schema subset | 4–18 s, extraction and structured reports |
| [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini) | $0.75 / $4.50 | 400K | Strict JSON Schema | 5–20 s, synthesis across report sections |
| [Claude Haiku 5.5](https://platform.claude.com/docs/en/models/haiku-5-5/overview) | $0.10 / $0.50 up to 100K input tokens | 1M | JSON Schema subset | 4–20 s, low-cost extraction and synthesis |

**Default: Gemini 3.1 Flash-Lite.** It supports structured output and provides a free quota for the demo. A report with 5,000 input and 1,800 output tokens costs about **$0.00395** at paid rates. Estimated end-to-end time is 6–30 seconds. **Fallback: GPT-5.4 mini**, about **$0.01185** at the same token counts.

The extractor limits prompt size by trimming boilerplate and duplicate content. For repeat requests, a short-lived cache keyed by content hash, prompt version, and model could reduce cost. Compare providers on evidence fidelity, schema pass rate, latency, and cost per usable report before changing the default.
