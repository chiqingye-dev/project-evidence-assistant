import pytest

from project_evidence.agent_protocol import SECTION_GOAL, ScriptedAdapter
from project_evidence.learning_store import compatible_parent
import project_evidence.learning_store as learning_module
from project_evidence.task_service import TaskService
from test_learning_core import setup, complete_outline, explanation


@pytest.mark.parametrize("version", ["0.4.0", "0.4.1", "0.4.2", "0.4.3", "0.4.4", "0.4.5"])
def test_upgrade_reuses_outline_without_rewriting_old_provenance(tmp_path, monkeypatch, version):
    env = setup(tmp_path)
    projects, record, tasks, lessons, eid, ref, _ = env
    monkeypatch.setattr(learning_module, "__version__", version)
    old = lessons.save_from_task(record.id, complete_outline(env)["id"])
    monkeypatch.setattr(learning_module, "__version__", "0.5.0")
    section = tasks.create(record, "scripted", {}, kind=SECTION_GOAL,
                           inputs={"outline_id": old["id"], "section_id": "entry"})
    tasks.update(record.id, section["id"], status="completed", result=explanation(eid), evidence=[ref])
    saved = lessons.save_from_task(record.id, section["id"])
    assert saved["producer_version"] == "0.5.0" and not saved["quality_verified"]
    preserved = lessons.get(record, old["id"])
    assert preserved["producer_version"] == version and preserved["content"] == old["content"]
    assert preserved["review_status"] == "needs_review"
    service = TaskService(projects, object(), ScriptedAdapter([]))
    assert service._anchor(record, SECTION_GOAL, {"outline_id": old["id"], "section_id": "entry"})


@pytest.mark.parametrize("payload", [
    {"producer_version": "unknown", "content_schema_version": 1},
    {"producer_version": "0.4.5", "content_schema_version": 99},
])
def test_unknown_version_or_schema_does_not_gain_compatibility(payload):
    assert not compatible_parent(payload)
