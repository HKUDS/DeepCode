import json
from pathlib import Path

from app_server.connection import ConnectionState
from app_server.dispatcher import Dispatcher
from core.application import DeepCodeApplication


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "protocol" / "app-server.schema.json"
GENERATED_TYPES = ROOT / "desktop" / "src" / "generated" / "app-server.ts"


def test_schema_covers_every_server_method(tmp_path: Path) -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    schema_methods = set(schema["$defs"]["MethodParams"]["properties"])
    application = DeepCodeApplication.open(tmp_path / "state.sqlite3")
    dispatcher = Dispatcher(application, ConnectionState(application.broker))
    assert schema_methods == set(dispatcher.methods)


def test_typescript_contract_is_generated_from_the_canonical_schema() -> None:
    generated = GENERATED_TYPES.read_text(encoding="utf-8")
    assert generated.startswith(
        "/* AUTO-GENERATED from protocol/app-server.schema.json. DO NOT EDIT. */"
    )
    assert "export interface MethodParams" in generated
    assert '"event/replay": EventReplayParams' in generated
