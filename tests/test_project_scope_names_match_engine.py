"""``beakr_cli.project_scope_names`` is the engine's project-name matcher, byte for byte.

Both MCP servers refuse a project name that names nothing with the closest
readable names. The stdio server used its own matcher with a looser cutoff
(0.4, over names and slugs) and so suggested names the hosted server did not;
it now carries a copy of the engine's ``beakr/services/project_scope_names.py``.
The two repositories share no package; this fails the moment the copy drifts.
"""

from pathlib import Path

from tests._engine import engine_checkout


def test_the_copy_matches_the_engine():
    engine = engine_checkout("beakr/services/project_scope_names.py")
    local = Path(__file__).resolve().parents[1] / "src/beakr_cli/project_scope_names.py"
    assert local.read_bytes() == engine.read_bytes()
