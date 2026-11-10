"""Pinned columns representation inputs; used only by explicit opt-in."""
from pathlib import Path
import hashlib,json
from .agent_protocol import ModelError
HERE=Path(__file__).resolve().parents[2]
MANIFEST='schemas/followup-scope-columns-bindings.json'
EXPECTED='980be8fc86f0ca75e4ca10f6e5d1022ac7315066e216efb5ad2f60d9b265757a'
def verify_inputs():
    try:
        manifest=HERE/MANIFEST
        if manifest.is_symlink() or hashlib.sha256(manifest.read_bytes()).hexdigest()!=EXPECTED:raise ValueError()
        for name,wanted in json.loads(manifest.read_text(encoding='utf-8')).items():
            p=HERE/name
            if p.is_symlink() or p.resolve()!=HERE.resolve()/name or hashlib.sha256(p.read_bytes()).hexdigest()!=wanted:raise ValueError()
    except (OSError,ValueError,TypeError):raise ModelError('not_authorized',request_sent=False) from None
    return EXPECTED
