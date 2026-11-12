import pytest
from test_task_storage import fixture_store


@pytest.mark.parametrize('value,retained',[('a'*64,True),('not-a-digest',False),('C:/Users/private/key',False),(True,False)])
def test_plan_event_keeps_only_a_digest_not_public_plan_or_sensitive_strings(tmp_path,value,retained):
    projects,record,tasks=fixture_store(tmp_path);task=tasks.create(record,'scripted',{})
    tasks.update(record.id,task['id'],status='running',event=dict(kind='coverage_plan_accepted',message='Bound only.',plan_digest=value,criteria=['not logged']))
    event=tasks.get(record.id,task['id'])['events'][-1]
    assert ('plan_digest' in event) is retained and 'criteria' not in event
