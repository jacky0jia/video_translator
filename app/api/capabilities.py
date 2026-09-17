from fastapi import APIRouter

from app.core.capabilities import get_capability_manifest


router = APIRouter()


@router.get("/capabilities")
async def get_capabilities():
    return get_capability_manifest().to_public_dict()
