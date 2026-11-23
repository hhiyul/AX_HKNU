import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.ai.runtime import get_runtime
from app.config import get_settings
from app.db import close_db, get_db
from app.errors import register_error_handlers
from app.routers import assets, auth, brands, jobs, openai_test, profile, projects, runs, workspace
from app.services import ai_runs


logger = logging.getLogger(__name__)


async def reap_stale_runs() -> None:
    """멈춘 AI 실행(서버가 꺼지며 남은 것)을 정리한다. DB에 닿지 않아도 서버는 뜬다."""
    if not get_settings().mongo_details:
        return
    try:
        await ai_runs.reap_stale(get_db())
    except Exception:
        logger.exception("Could not reap stale AI runs at startup")


async def stop_running_runs(timeout: float = 5) -> None:
    """서버를 끌 때 이 서버에서 도는 AI 실행을 중지해, 부분 답변을 남기고 프로젝트 잠금을 풀게 한다."""
    tasks = list(get_runtime().tasks.values())
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.wait(tasks, timeout=timeout)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # 다른 서버의 살아 있는 실행은 건드리지 않는다(60초 넘게 멈춘 것만).
    await reap_stale_runs()
    yield
    await stop_running_runs()
    await close_db()


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(brands.router)
app.include_router(projects.router)
for workspace_router in workspace.routers:
    app.include_router(workspace_router)
app.include_router(assets.router)
app.include_router(jobs.router)
app.include_router(runs.router)
app.include_router(openai_test.router)

#연결테스트 코드
#localhost/api/ 프론트 nginx > 백엔드  | localhost:8000/docs swagger | localhost:8000/hello/hknu 백엔드 직접 | localhost 프론트

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}



