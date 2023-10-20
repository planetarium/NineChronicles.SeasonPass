from fastapi import APIRouter
from season_pass.api import season_pass

router = APIRouter(
    prefix="/api",
    tags=["API"],
)

__all__ = [
    season_pass,
]

for view in __all__:
    router.include_router(view.router)
