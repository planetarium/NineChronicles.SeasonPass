from fastapi import APIRouter
from season_pass.api import season_pass, user, tmp

router = APIRouter(
    prefix="/api",
    tags=["API"],
)

__all__ = [
    season_pass,
    user,
    tmp,
]

for view in __all__:
    router.include_router(view.router)
