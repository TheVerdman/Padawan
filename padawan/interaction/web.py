import json
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from padawan.interaction.composition import InteractionApplication
from padawan.interaction.contracts import InteractionFeedbackKind
from padawan.interaction.service import (
    GenerationSubmission,
    InteractionStreamUpdate,
    TemporaryGenerationSubmission,
)

_COOKIE = "padawan_interaction_session"


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_token: str = Field(min_length=1)


class CreateSessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=256)
    research_trace_consent: bool = False


class ConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    research_trace_consent: bool


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: InteractionFeedbackKind
    body: str | None = None


class _BrowserSessions:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token
        self.csrf_by_session: dict[str, str] = {}

    def login(self, candidate: str) -> tuple[str, str]:
        if not secrets.compare_digest(candidate, self.access_token):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid login")
        session_id = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        self.csrf_by_session[session_id] = csrf
        return session_id, csrf

    def authenticate(self, request: Request) -> str:
        session_id = request.cookies.get(_COOKIE)
        csrf = self.csrf_by_session.get(session_id or "")
        if csrf is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login required")
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
            request.headers.get("X-Padawan-CSRF", ""), csrf
        ):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF check failed")
        return csrf

    def logout(self, request: Request) -> None:
        session_id = request.cookies.get(_COOKIE)
        if session_id is not None:
            self.csrf_by_session.pop(session_id, None)


def create_interaction_web_app(
    application: InteractionApplication,
    *,
    access_token: str,
    close_application: bool = True,
) -> FastAPI:
    if len(access_token) < 16:
        raise ValueError("Interaction Lab access token must contain at least 16 characters")
    browser_sessions = _BrowserSessions(access_token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            if close_application:
                await application.close()

    app = FastAPI(
        title="Padawan Interaction Lab",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    def require_auth(request: Request) -> str:
        return browser_sessions.authenticate(request)

    auth_dependency = Annotated[str, Depends(require_auth)]

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Any) -> Response:
        response: Response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/", response_class=HTMLResponse)
    async def index() -> HTMLResponse:
        return HTMLResponse(_asset("index.html"))

    @app.get("/assets/app.js")
    async def javascript() -> Response:
        return Response(_asset("app.js"), media_type="application/javascript")

    @app.get("/assets/styles.css")
    async def stylesheet() -> Response:
        return Response(_asset("styles.css"), media_type="text/css")

    @app.post("/api/auth/login")
    async def login(payload: LoginRequest, response: Response) -> dict[str, str]:
        session_id, csrf = browser_sessions.login(payload.access_token)
        response.set_cookie(
            _COOKIE,
            session_id,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
        )
        return {"csrf_token": csrf}

    @app.post("/api/auth/logout")
    async def logout(
        request: Request, response: Response, _auth: auth_dependency
    ) -> dict[str, bool]:
        browser_sessions.logout(request)
        response.delete_cookie(_COOKIE, path="/")
        return {"logged_out": True}

    @app.get("/api/bootstrap")
    async def bootstrap(csrf: auth_dependency) -> dict[str, Any]:
        async with application.database.transaction() as session:
            sessions = await application.store.list_sessions(session)
        return {
            "product": "Padawan Interaction Lab",
            "csrf_token": csrf,
            "targets": [
                {
                    "target_id": target.target_id,
                    "display_name": target.display_name,
                    "provider": target.provider,
                    "status": (await target.readiness(refresh=False)).model_dump(mode="json"),
                }
                for target in application.targets.list()
            ],
            "sessions": [item.model_dump(mode="json") for item in sessions],
            "temporary_chat": {
                "durable_storage": False,
                "creates_research_trace": False,
                "creates_memory": False,
                "creates_training_candidate": False,
            },
        }

    @app.post("/api/targets/{target_id}/readiness")
    async def readiness(target_id: str, _auth: auth_dependency) -> dict[str, Any]:
        try:
            target = application.targets.get(target_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return (await target.readiness(refresh=True)).model_dump(mode="json")

    @app.get("/api/sessions")
    async def list_sessions(_auth: auth_dependency) -> dict[str, Any]:
        async with application.database.transaction() as session:
            records = await application.store.list_sessions(session)
        return {"sessions": [item.model_dump(mode="json") for item in records]}

    @app.post("/api/sessions", status_code=201)
    async def create_session(
        payload: CreateSessionRequest, _auth: auth_dependency
    ) -> dict[str, Any]:
        async with application.database.transaction() as session:
            record = await application.store.create_session(
                session,
                title=payload.title,
                research_trace_consent=payload.research_trace_consent,
            )
        return record.model_dump(mode="json")

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str, _auth: auth_dependency) -> dict[str, Any]:
        try:
            async with application.database.transaction() as session:
                return await application.store.conversation(session, session_id=session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc

    @app.delete("/api/sessions/{session_id}")
    async def delete_session(session_id: str, _auth: auth_dependency) -> dict[str, Any]:
        try:
            async with application.database.transaction() as session:
                result = await application.store.delete_session(session, session_id=session_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return {"session_id": session_id, **result}

    @app.post("/api/sessions/{session_id}/consent")
    async def update_consent(
        session_id: str, payload: ConsentRequest, _auth: auth_dependency
    ) -> dict[str, Any]:
        try:
            async with application.database.transaction() as session:
                consent = await application.store.append_consent(
                    session,
                    session_id=session_id,
                    research_trace_consent=payload.research_trace_consent,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="session not found") from exc
        return consent.model_dump(mode="json")

    @app.post("/api/sessions/{session_id}/generations")
    async def generate(
        session_id: str, payload: GenerationSubmission, _auth: auth_dependency
    ) -> StreamingResponse:
        return _streaming_response(
            application.service.stream_generation(session_id=session_id, submission=payload)
        )

    @app.post("/api/temporary/generations")
    async def generate_temporary(
        payload: TemporaryGenerationSubmission, _auth: auth_dependency
    ) -> StreamingResponse:
        return _streaming_response(application.service.stream_temporary(payload))

    @app.post("/api/turns/{turn_id}/feedback", status_code=201)
    async def feedback(
        turn_id: str, payload: FeedbackRequest, _auth: auth_dependency
    ) -> dict[str, Any]:
        try:
            async with application.database.transaction() as session:
                record = await application.store.append_feedback(
                    session,
                    turn_id=turn_id,
                    kind=payload.kind,
                    body=payload.body,
                )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="turn not found") from exc
        return record.model_dump(mode="json")

    @app.get("/api/traces/{trace_id}")
    async def trace(
        trace_id: str,
        _auth: auth_dependency,
        include_restricted: bool = False,
        research_trace_view: Annotated[str | None, Header(alias="X-Padawan-Research-Trace")] = None,
    ) -> dict[str, Any]:
        if include_restricted and research_trace_view != "reveal":
            raise HTTPException(
                status_code=403,
                detail="restricted trace access requires an explicit research-view header",
            )
        try:
            return await application.service.trace_inspector(
                trace_id=trace_id, include_restricted=include_restricted
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="trace not found") from exc

    @app.get("/api/traces/{trace_id}/private-reasoning")
    async def private_reasoning(
        trace_id: str,
        _auth: auth_dependency,
        research_trace_view: Annotated[str | None, Header(alias="X-Padawan-Research-Trace")] = None,
    ) -> dict[str, str | None]:
        if research_trace_view != "reveal":
            raise HTTPException(
                status_code=403,
                detail="private reasoning requires an explicit research-view header",
            )
        try:
            return await application.service.private_reasoning_view(trace_id=trace_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="trace not found") from exc

    return app


def _streaming_response(
    updates: AsyncIterator[InteractionStreamUpdate],
) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        async for update in updates:
            payload = json.dumps(update.data, separators=(",", ":"), ensure_ascii=False)
            yield f"event: {update.event}\ndata: {payload}\n\n".encode()

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _asset(name: str) -> str:
    return (
        files("padawan.interaction").joinpath("static").joinpath(name).read_text(encoding="utf-8")
    )
