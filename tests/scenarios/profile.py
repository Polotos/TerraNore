import argparse
import json

from .performance import profile_scenario

parser = argparse.ArgumentParser()
parser.add_argument("--years", type=int, default=100)
args = parser.parse_args()
print(json.dumps(profile_scenario(args.years), indent=2, sort_keys=True))
