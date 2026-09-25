from fastapi import APIRouter

from ..services import emulator_client

router = APIRouter(prefix="/api", tags=["emulator"])


@router.get("/cells")
async def cells():
    return await emulator_client.get_cells()


@router.get("/config")
async def config_get():
    return await emulator_client.get_config()


@router.post("/config")
async def config_set(payload: dict):
    return await emulator_client.set_config(payload)