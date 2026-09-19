import argparse
import json

from . import Simulation

parser = argparse.ArgumentParser(description="TerraNore Test headless simulation")
parser.add_argument("--years", type=int, default=100)
parser.add_argument("--seed", type=int, default=42)
args = parser.parse_args()
world = Simulation(args.seed).run_years(args.years)
print(json.dumps(world.to_dict(), ensure_ascii=False, separators=(",", ":")))
