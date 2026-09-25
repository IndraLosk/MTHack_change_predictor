from contextlib import asynccontextmanager

from fastapi import FastAPI

from . import settings
from .ndtp import receiver
from .routes import emulator, system, telemetry


@asynccontextmanager
async def lifespan(app: FastAPI):
    server = await receiver.start_receiver(settings.NDTP_RECEIVE_PORT)
    app.state.ndtp_server = server
    yield
    server.close()
    await server.wait_closed()


app = FastAPI(
    title="MTHack Backend",
    description="Backend для хакатона Московского транспорта: приём NDTP, признаки, API.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(system.router)
app.include_router(emulator.router)
app.include_router(telemetry.router)