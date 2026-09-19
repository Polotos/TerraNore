from dataclasses import dataclass, field

from src.simulation.model import World


@dataclass
class TimeSeries:
    points: list[dict] = field(default_factory=list)

    def capture(self, world: World) -> None:
        self.points.append({
            "tick": world.tick,
            "population": sum(region.population for region in world.regions),
            "treasury": round(sum(region.economy.treasury for region in world.regions), 2),
        })
