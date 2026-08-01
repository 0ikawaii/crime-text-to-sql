"""
Step 5 — FastAPI wrapper around the text-to-SQL pipeline.

Run with:
    uvicorn app:app --reload

Then visit http://localhost:8000/docs for the auto-generated OpenAPI UI,
or POST to http://localhost:8000/query directly.

CORS is enabled because the Vue frontend (Step 9b) runs in the browser and
calls this API directly from a different origin (e.g. http://localhost:5173
in dev, or wherever the built static site is served from) — unlike the old
Streamlit UI, which made server-side requests and never hit a CORS check.
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from text_to_sql_core import answer_question, SQLRejectedError

app = FastAPI(
    title="Crime Text-to-SQL Assistant",
    description="Ask natural-language questions about Malaysian crime, income, "
    "and population data by district.",
    version="0.1.0",
)

# Dev-friendly wide-open CORS. Fine for a local learning project; if this
# ever goes anywhere beyond localhost, replace allow_origins=["*"] with an
# explicit list of trusted frontend origins.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class QueryRequest(BaseModel):
    question: str


class QueryResponse(BaseModel):
    question: str
    sql: str
    columns: list[str]
    rows: list[list]
    row_count: int


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/query", response_model=QueryResponse)
def query(request: QueryRequest):
    if not request.question or not request.question.strip():
        raise HTTPException(status_code=400, detail="question must not be empty")

    try:
        result = answer_question(request.question)
    except SQLRejectedError as e:
        # Generated SQL failed the sqlglot safety check — surface as a client
        # error (the question, not the server, produced something unsafe).
        raise HTTPException(status_code=422, detail=str(e))
    except RuntimeError as e:
        # e.g. missing ANTHROPIC_API_KEY
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        # DB errors (bad SQL syntax the LLM produced, timeout, etc.)
        raise HTTPException(status_code=500, detail=f"Query execution failed: {e}")

    return result