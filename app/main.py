import logging
import os
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from app.api import config, transcribe, translation, tasks, fs, burn, dubbing, pipeline
from app.core.config import settings
from app.core.history import history_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

# Workaround for Windows asyncio bug: suppress ConnectionResetError on SSE client disconnect
if sys.platform == 'win32':
    import asyncio
    from asyncio.proactor_events import _ProactorBasePipeTransport

    def _silence_connection_reset(func):
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except ConnectionResetError:
                pass
        return wrapper

    _ProactorBasePipeTransport._call_connection_lost = _silence_connection_reset(
        _ProactorBasePipeTransport._call_connection_lost
    )

@asynccontextmanager
async def lifespan(app: FastAPI):
    history_manager.recover_interrupted_tasks()
    try:
        yield
    finally:
        from app.services.pipeline_service import pipeline_service
        await pipeline_service.shutdown()

app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

# API routers must be registered BEFORE the catch-all SPA mount
app.include_router(config.router, prefix="/api", tags=["Config"])
app.include_router(transcribe.router, prefix="/api", tags=["Transcription"])
app.include_router(translation.router, prefix="/api", tags=["Translation"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(burn.router, prefix="/api", tags=["Burn"])
app.include_router(dubbing.router, prefix="/api", tags=["Dubbing"])
app.include_router(pipeline.router, prefix="/api", tags=["Pipeline"])
app.include_router(fs.router, prefix="/api", tags=["FileSystem"])


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Catch-all to prevent internal tracebacks from leaking to clients."""
    logging.exception("Unhandled exception")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})

# Serve uploaded videos for preview with Range request support (enables video seek)
@app.get("/video/{filename}")
async def video_stream(filename: str, request: Request):
    # Prevent path traversal: strip any path components and resolve within UPLOAD_DIR
    safe_name = os.path.basename(filename)
    file_path = os.path.realpath(os.path.join(settings.UPLOAD_DIR, safe_name))
    upload_dir = os.path.realpath(settings.UPLOAD_DIR)
    if not file_path.startswith(upload_dir) or not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Video not found")
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")
    if range_header:
        try:
            h = range_header.replace("bytes=", "").split("-")
            start = int(h[0]) if h[0] else 0
            end = int(h[1]) if h[1] else file_size - 1
        except Exception:
            start, end = 0, file_size - 1
        if start >= file_size:
            raise HTTPException(status_code=416, detail="Range not satisfiable")
        end = min(end, file_size - 1)
        length = end - start + 1
        def iterfile():
            with open(file_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk_size = min(8192, remaining)
                    data = f.read(chunk_size)
                    if not data:
                        break
                    remaining -= len(data)
                    yield data
        headers = {
            "Content-Range": f"bytes {start}-{end}/{file_size}",
            "Accept-Ranges": "bytes",
            "Content-Length": str(length),
        }
        return StreamingResponse(iterfile(), status_code=206, headers=headers, media_type="video/mp4")
    return FileResponse(file_path, media_type="video/mp4")

# Fallback static uploads mount for non-video files
app.mount("/static/uploads", StaticFiles(directory=settings.UPLOAD_DIR), name="uploads")

# Serve output JSON subtitle files
app.mount("/static/output", StaticFiles(directory=settings.OUTPUT_DIR), name="output")

# Serve React SPA (fallback to index.html for client-side routing)
app.mount("/", StaticFiles(directory="app/frontend/dist", html=True), name="frontend")
