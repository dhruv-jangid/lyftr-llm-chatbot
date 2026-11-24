"""FastAPI app: the three endpoints of the API contract + JSON error handling."""
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db, llm
from .config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("app")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()  # create /data/app.db and tables if missing
    logger.info("startup: %d key(s) configured, model=%s", len(settings.api_keys), settings.llm_model)
    yield


app = FastAPI(title="Lyftr LLM Chatbot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


# ---------- request / response models ----------

class ChatRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=128)
    message: str
    system_prompt: str | None = Field(default=None, max_length=4000)

    @field_validator("session_id")
    @classmethod
    def session_id_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("session_id must not be empty")
        return v

    @field_validator("message")
    @classmethod
    def message_length(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("message must not be empty")
        if len(v) > 4000:
            raise ValueError("message must be at most 4000 characters")
        return v

    @field_validator("system_prompt")
    @classmethod
    def blank_system_prompt_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v is not None else None


class Metadata(BaseModel):
    resolution_source: str
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int


class ChatResponse(BaseModel):
    session_id: str
    reply: str
    metadata: Metadata


# ---------- error handlers: always JSON with a "detail" field ----------

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    # Same shape as FastAPI's default, minus the echoed input.
    errors = [{"loc": e.get("loc"), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    logger.exception("unhandled error")  # full trace goes to server logs only
    return JSONResponse(status_code=500, content={"detail": "internal server error"})


# ---------- endpoints ----------

@app.get("/health")
def health():
    keys_ok = len(settings.api_keys) >= 2
    db_ok = db.ping()
    body = {
        "status": "ok" if (keys_ok and db_ok) else "unavailable",
        "keys_configured": len(settings.api_keys),
        "cache_enabled": llm.litellm.cache is not None,
    }
    if not (keys_ok and db_ok):
        body["detail"] = "database unreachable" if not db_ok else "key pool needs at least 2 keys"
        return JSONResponse(status_code=503, content=body)
    return body


@app.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    x_simulate_failure: str | None = Header(default=None, alias="X-Simulate-Failure"),
):
    started = time.perf_counter()

    simulated: dict[str, int] = {}
    if settings.enable_failure_simulation and x_simulate_failure:
        try:
            simulated = llm.parse_simulation_header(x_simulate_failure)
        except llm.InvalidSimulationHeader as exc:
            return JSONResponse(status_code=422, content={"detail": str(exc)})

    # 1) Load earlier turns from SQLite (unknown session -> empty -> new session)
    history = db.get_history(body.session_id)
    system_prompt = (
        body.system_prompt
        or db.get_session_system_prompt(body.session_id)
        or settings.default_system_prompt
    )

    # 2) Build the full message list: system + history + new message
    messages = [{"role": "system", "content": system_prompt}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": body.message})

    # 3) Ask the LLM (LiteLLM handles cache + key failover)
    try:
        result = await llm.complete(messages, simulated)
    except llm.AllProvidersFailed:
        return JSONResponse(status_code=503, content={"detail": "all providers unavailable"})

    latency_ms = int(round((time.perf_counter() - started) * 1000))
    metadata = {
        "resolution_source": result.resolution_source,
        "model": settings.llm_model,
        "latency_ms": latency_ms,
        "input_tokens": result.input_tokens,
        "output_tokens": result.output_tokens,
        "key_id": result.key_id,
        "fallback_attempts": result.fallback_attempts,
    }

    # 4) Only a successful exchange is stored
    db.save_exchange(body.session_id, body.message, result.reply, metadata, body.system_prompt)

    return {"session_id": body.session_id, "reply": result.reply, "metadata": metadata}


@app.get("/sessions/{session_id}/messages")
def get_messages(session_id: str):
    history = db.get_history(session_id)
    if not history:
        return JSONResponse(status_code=404, content={"detail": "session not found"})
    return {"session_id": session_id, "messages": history}
