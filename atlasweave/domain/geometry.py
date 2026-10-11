"""World coordinates and scale-dependent tolerances (§3.4, §4.7)."""

from dataclasses import dataclass

from atlasweave.domain.values import ModelError, number_value


@dataclass(frozen=True)
class Point:
    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", number_value(self.x, "x"))
        object.__setattr__(self, "y", number_value(self.y, "y"))

    def as_tuple(self) -> tuple[float, float]:
        return self.x, self.y


@dataclass(frozen=True)
class Tolerances:
    coordinate_epsilon: float
    minimum_edge_length: float
    minimum_face_area: float

    def __post_init__(self) -> None:
        for name in ("coordinate_epsilon", "minimum_edge_length", "minimum_face_area"):
            value = number_value(getattr(self, name), name, minimum=0)
            if value == 0:
                raise ModelError(name, "容差必须大于零")
            object.__setattr__(self, name, value)

    @classmethod
    def for_world(cls, width: float, height: float) -> "Tolerances":
        width = number_value(width, "world_width_km", minimum=1, maximum=1_000_000)
        height = number_value(height, "world_height_km", minimum=1, maximum=1_000_000)
        scale = max(width, height)
        return cls(scale * 1e-9, scale * 1e-6, width * height * 1e-10)
