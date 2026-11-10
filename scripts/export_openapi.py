"""Write FastAPI's OpenAPI schema to apps/web/openapi.json (input for the generated TS client)."""
import json

from agent.settings import ROOT
from apps.api.main import app

(ROOT / "apps/web/openapi.json").write_text(json.dumps(app.openapi(), indent=1))
print("wrote apps/web/openapi.json")
