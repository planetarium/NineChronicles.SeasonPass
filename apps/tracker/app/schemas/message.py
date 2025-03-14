from pydantic import BaseModel


class Message(BaseModel):
    planet_id: str
    block: int
    action_data: dict
