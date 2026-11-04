import json
from pathlib import Path

from main import app

CONTRACT_PATH = Path(__file__).parents[4] / "contracts" / "catalog.openapi.json"


def render_contract() -> str:
    return json.dumps(app.openapi(), indent=2) + "\n"


if __name__ == "__main__":
    CONTRACT_PATH.parent.mkdir(exist_ok=True)
    CONTRACT_PATH.write_text(render_contract())
