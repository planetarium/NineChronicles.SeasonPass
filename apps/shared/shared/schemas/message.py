from pydantic import BaseModel


class TrackerMessage(BaseModel):
    planet_id: str
    block: int
    action_data: dict


class ClaimMessage(BaseModel):
    uuid: str
