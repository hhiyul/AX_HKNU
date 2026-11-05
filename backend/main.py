from fastapi import FastAPI

app = FastAPI()

#연결테스트 코드
#localhost:8080/api/ 게이트 > 백엔드  | localhost:8000/docs swagger | localhost:8080/actuator/health  게이트웨이 | localhost:8000/hello/hknu 백엔드 직접 | localhost 프론트

@app.get("/")
async def root():
    return {"message": "Hello World"}


@app.get("/hello/{name}")
async def say_hello(name: str):
    return {"message": f"Hello {name}"}
