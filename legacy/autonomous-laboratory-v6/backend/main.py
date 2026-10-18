"""HTTP interface for the laboratory control system.

Startup loads hardware.yaml, connects the controller, and registers device
operations. GET /api/config exposes the shared configuration. GET /fn lists
operation schemas, and POST /fn/{name} invokes an operation. GET /api/stream
publishes device state, scale measurements, and serial logs as server-sent events.
Device logic is implemented in backend/functions/.
"""
from __future__ import annotations

import asyncio
import json
import logging
import queue
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import registry
from .config import get_config
from .functions import MODULES  # noqa: F401  Register operations on import.
from .functions import scale as scale_fn
from .registry import Context, FunctionError
from .transport import Link, LinkError, get_link

log = logging.getLogger("lab")
ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"
DOCS = ROOT / "docs"


def _sync_scale_runtime(link: Link, cfg: Dict[str, Any]) -> None:
    """Restore configured filtering, stability threshold, and stream rate after reset."""
    scale = cfg["scale"]
    link.send(f"W,AVG,{int(scale['average_samples'])}")
    link.send(f"W,STABLE,{float(scale['stable_threshold_g']):g}")
    link.send(f"W,STREAM,{int(scale['stream_hz'])}")
    link.refresh_state()


@asynccontextmanager
async def lifespan(app: FastAPI):
    cfg = get_config()
    link = get_link()
    try:
        link.start()
        log.info("Connected to %s", link.port_name)
        try:
            restored = scale_fn.restore_calibration(link)
            if restored:
                log.info("Loaded scale calibration from %s", restored["file"])
                if restored.get("warning"):
                    log.warning(restored["warning"])
            else:
                log.info("No scale calibration file found; startup checks %s",
                         scale_fn.calibration_path())
        except Exception as exc:
            log.warning("Scale calibration could not be loaded; service remains available: %s", exc)
        # Apply the shared YAML settings after every controller reset, including
        # runtime filtering, stability, and stream rate, instead of relying on flash defaults.
        _sync_scale_runtime(link, cfg)
        log.info("Scale settings synchronized: AVG=%s, STABLE=%s g, STREAM=%s Hz",
                 cfg["scale"]["average_samples"],
                 cfg["scale"]["stable_threshold_g"],
                 cfg["scale"]["stream_hz"])
    except LinkError as exc:
        log.warning("Arduino is not connected: %s (the interface remains available)", exc)
    if scale_fn.record not in link.weight_hooks:
        link.weight_hooks.append(scale_fn.record)
    app.state.link = link
    app.state.config = cfg
    yield
    link.stop()


app = FastAPI(title="Autonomous Laboratory v6", version="6.0.0", lifespan=lifespan,
              docs_url="/api/docs", redoc_url=None)   # Reserve /docs for wiring documentation.
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_config()["server"].get("cors_origins", ["*"]),
    allow_methods=["*"], allow_headers=["*"],
)


def ctx() -> Context:
    return Context(link=get_link(), config=get_config())


# ---------------------------------------------------------------------
# Configuration and operation manifest
# ---------------------------------------------------------------------
@app.get("/api/config")
def api_config() -> Dict[str, Any]:
    cfg = get_config()
    link = get_link()
    return {"config": cfg, "link": {"connected": link.connected,
                                    "simulated": link.simulated,
                                    "port": link.port_name,
                                    "error": link.last_error},
            "calibration": scale_fn.calibration_status()}


@app.get("/fn")
def api_functions() -> Dict[str, Any]:
    return {"functions": registry.manifest()}


@app.get("/api/state")
def api_state() -> Dict[str, Any]:
    return get_link().snapshot()


@app.get("/api/log")
def api_log(limit: int = 200) -> Dict[str, Any]:
    return {"log": get_link().log[-limit:]}


@app.post("/api/reconnect")
def api_reconnect() -> Dict[str, Any]:
    link = get_link()
    link.stop()
    try:
        link.start()
        restored = scale_fn.restore_calibration(link)
        _sync_scale_runtime(link, get_config())
    except LinkError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Reconnected, but calibration could not be restored: {exc}")
    return {"connected": link.connected, "port": link.port_name,
            "calibration_restored": bool(restored)}


# ---------------------------------------------------------------------
# Shared operation invocation endpoint
# ---------------------------------------------------------------------
@app.post("/fn/{name}")
async def api_invoke(name: str, request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Request body must contain valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object")

    started = time.perf_counter()
    try:
        fn = registry.get(name)
        result = await asyncio.to_thread(fn.invoke, ctx(), payload)
    except FunctionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LinkError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:                       # pragma: no cover
        log.exception("Operation %s failed", name)
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")

    return JSONResponse({
        "ok": True, "function": name, "result": result,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    })


# ---------------------------------------------------------------------
# Live event stream
# ---------------------------------------------------------------------
@app.get("/api/stream")
async def api_stream(request: Request) -> StreamingResponse:
    link = get_link()
    q: queue.Queue = link.subscribe()

    async def gen():
        # Starlette closes the generator on disconnect; finally removes the subscription.
        # Avoid request.is_disconnected(), which can block in some streaming contexts.
        yield _sse({"type": "state", "data": link.snapshot()})
        last_ping = time.time()
        try:
            while True:
                try:
                    event = await asyncio.to_thread(q.get, True, 1.0)
                    yield _sse(event)
                except queue.Empty:
                    if time.time() - last_ping > 15:
                        last_ping = time.time()
                        yield ": ping\n\n"
        finally:
            link.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"})


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"


# ---------------------------------------------------------------------
# Static pages
# ---------------------------------------------------------------------
@app.get("/")
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/docs")
@app.get("/docs/")
def docs_index() -> FileResponse:
    return FileResponse(DOCS / "index.html")


@app.get("/docs/{name}")
def doc(name: str) -> FileResponse:
    target = (DOCS / name).resolve()
    if target.parent != DOCS.resolve() or not target.exists():
        raise HTTPException(status_code=404, detail="Document not found")
    return FileResponse(target)


def run() -> None:
    import uvicorn
    cfg = get_config()["server"]
    uvicorn.run(app, host=cfg.get("host", "127.0.0.1"),
                port=int(cfg.get("port", 8000)), log_level="info")


if __name__ == "__main__":
    run()
