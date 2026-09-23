import json, os
def coins27():
    u = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "coin_universe.json")))
    return [p for p, v in u.items() if v.get("years", 0) >= 5]
