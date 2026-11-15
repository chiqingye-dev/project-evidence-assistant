"""Pinned opt-in request/decoder/engine/readiness inputs; no authority changes."""
from pathlib import Path
import hashlib
import json
from .agent_protocol import ModelError

HERE=Path(__file__).resolve().parents[2]
MANIFEST='schemas/section-native-bindings.json'
EXPECTED='a01b067f4ffd01a23594faee46a8404c882fb52073af94bf12db84399682f854'


def verify_inputs():
    try:
        manifest=HERE/MANIFEST
        if manifest.is_symlink() or hashlib.sha256(manifest.read_bytes()).hexdigest()!=EXPECTED: raise ValueError
        for name,wanted in json.loads(manifest.read_text(encoding='utf-8')).items():
            path=HERE/name
            if (path.is_symlink() or path.resolve()!=HERE.resolve()/name
                    or hashlib.sha256(path.read_bytes()).hexdigest()!=wanted): raise ValueError
    except (OSError,ValueError,TypeError):
        raise ModelError('not_authorized',request_sent=False) from None
    return EXPECTED
