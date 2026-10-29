"""A small HTTP surface over the lab, so the pieces can be shown working together. Authentication is out of scope: the
caller names itself in ``X-User`` (a user id). Everything below runs as that user in the named tenant, through the same
router and the same row-level security as the tests."""

from __future__ import annotations

from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request

from dclabaccount import exceptions
from dclabaccount.db import NotFound, Router
from dclabaccount.imports import import_saft
from dclabaccount.queries import QUERIES


def create_app(router: Router | None = None) -> FastAPI:
    app = FastAPI(title="DcLabAccount", docs_url=None, redoc_url=None)
    app.state.router = router or Router()

    def user(x_user: str | None) -> UUID:
        try:
            return UUID(x_user or "")
        except ValueError:
            raise HTTPException(status_code=401, detail="X-User must be a user id") from None

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/t/{slug}/{query}")
    def tenant_query(slug: str, query: str, x_user: str | None = Header(default=None)) -> list[dict]:
        spec = QUERIES.get(query)
        if spec is None or query == "forgot_the_filter":
            raise HTTPException(status_code=404, detail="no such query")
        try:
            with app.state.router.tenant_tx(slug, user(x_user), store=spec.store) as conn:
                return spec.run(conn)
        except NotFound:
            raise HTTPException(status_code=404, detail="tenant not found") from None  # one answer for missing and forbidden

    @app.post("/t/{slug}/imports")
    async def upload(slug: str, request: Request, x_user: str | None = Header(default=None), x_filename: str = Header(default="upload.xml")) -> dict:
        content = await request.body()
        try:
            result = import_saft(app.state.router, user(x_user), slug, x_filename, content)
        except NotFound:
            raise HTTPException(status_code=404, detail="tenant not found") from None
        return {
            "status": result.status, "period": result.period, "lines": result.lines, "replaced_lines": result.replaced_lines,
            "problems": [{"code": p.code, "message": p.message} for p in result.problems],
        }

    @app.get("/me/work")
    def my_work(x_user: str | None = Header(default=None)) -> list[dict]:
        return exceptions.run(app.state.router, user(x_user), "my_work_across_clients")

    return app
