from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import close_db
from app.errors import register_error_handlers
from app.routers import auth, brands, profile, projects


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

#연결테스트 코드
#localhost:8080/api/ 게이트 > 백엔드  | localhost:8000/docs swagger | localhost:8080/actuator/health  게이트웨이 | localhost:8000/hello/hknu 백엔드 직접 | localhost 프론트

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}



