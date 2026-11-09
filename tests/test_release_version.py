"""Release identity and old learning records retain their real provenance."""
from pathlib import Path
import tomllib
from project_evidence import __version__
from project_evidence.app import create_app


def test_package_api_and_release_metadata_have_one_version(tmp_path):
    metadata=tomllib.loads((Path(__file__).parents[1]/'pyproject.toml').read_text(encoding='utf-8'))
    assert metadata['project']['version']==__version__==create_app(data_dir=tmp_path/'db').version
