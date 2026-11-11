from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import close_db
from app.errors import register_error_handlers
from app.routers import assets, auth, brands, jobs, openai_test, plan, profile, projects, workspace


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    await close_db()


app = FastAPI(lifespan=lifespan)
register_error_handlers(app)
app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(brands.router)
app.include_router(projects.router)
app.include_router(plan.router)
for workspace_router in workspace.routers:
    app.include_router(workspace_router)
app.include_router(assets.router)
app.include_router(jobs.router)
app.include_router(openai_test.router)

#연결테스트 코드
#localhost/api/ 프론트 nginx > 백엔드  | localhost:8000/docs swagger | localhost:8000/hello/hknu 백엔드 직접 | localhost 프론트

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}



