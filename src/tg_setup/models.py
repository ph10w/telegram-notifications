from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DialogInfo:
    id: int
    kind: str
    name: str
