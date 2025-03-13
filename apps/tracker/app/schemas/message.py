from dataclasses import asdict, dataclass


@dataclass
class Message:
    planet_id: str
    block: int
    action_data: dict

    def to_dict(self):
        return asdict(self)
