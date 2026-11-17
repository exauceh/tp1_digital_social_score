"""``/score`` sheds load past the limit; probes and metrics never do."""

import asyncio

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from tp1_digital_social_score.concurrency_limit import ConcurrencyLimitMiddleware


def build_app(release: asyncio.Event, shed: list[int], max_in_flight: int = 1) -> Starlette:
    async def score(request: Request) -> Response:
        if request.query_params.get("fail"):
            raise RuntimeError("boom")
        await release.wait()
        return JSONResponse({"ok": True})

    async def health(request: Request) -> Response:
        return JSONResponse({"status": "ok"})

    app = Starlette(routes=[Route("/score", score, methods=["POST"]), Route("/health", health)])
    app.add_middleware(
        ConcurrencyLimitMiddleware,
        max_in_flight=max_in_flight,
        on_shed=lambda: shed.append(1),
    )
    return app


def client_of(app: Starlette) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


async def settle() -> None:
    await asyncio.sleep(0.05)


def test_a_request_over_the_limit_gets_503_with_retry_after() -> None:
    async def scenario() -> None:
        release, shed = asyncio.Event(), list[int]()
        async with client_of(build_app(release, shed)) as client:
            first = asyncio.create_task(client.post("/score"))
            await settle()
            refused = await client.post("/score")
            assert refused.status_code == 503
            assert refused.headers["Retry-After"] == "1"
            assert shed == [1]
            release.set()
            assert (await first).status_code == 200

    asyncio.run(scenario())


def test_health_still_answers_while_score_is_full() -> None:
    async def scenario() -> None:
        release, shed = asyncio.Event(), list[int]()
        async with client_of(build_app(release, shed)) as client:
            first = asyncio.create_task(client.post("/score"))
            await settle()
            assert (await client.get("/health")).status_code == 200
            assert shed == []
            release.set()
            await first

    asyncio.run(scenario())


def test_the_slot_is_freed_once_a_request_finishes() -> None:
    async def scenario() -> None:
        release, shed = asyncio.Event(), list[int]()
        release.set()
        async with client_of(build_app(release, shed)) as client:
            for _ in range(3):
                assert (await client.post("/score")).status_code == 200
        assert shed == []

    asyncio.run(scenario())


def test_the_slot_is_freed_when_the_handler_raises() -> None:
    async def scenario() -> None:
        release, shed = asyncio.Event(), list[int]()
        release.set()
        async with client_of(build_app(release, shed)) as client:
            assert (await client.post("/score?fail=1")).status_code == 500
            assert (await client.post("/score")).status_code == 200
        assert shed == []

    asyncio.run(scenario())
