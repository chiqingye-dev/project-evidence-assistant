from hashlib import sha256
from copy import deepcopy
import pytest
from project_evidence.source_coverage_inventory import inventory,scan,check_plan,digest

def read(text,eid='e1'):
 return dict(evidence_id=eid,path='src/Example.java',start_line=1,end_line=len(text.split('\n')),text=text,excerpt_sha256=sha256(text.encode()).hexdigest(),truncated=False)

SQL='''@Select("""
SELECT (SELECT COUNT(*) FROM alpha WHERE status IN ('WAIT', 'RETRY'))
+ (SELECT COUNT(*) FROM beta WHERE status='WAIT')
""")'''

def test_each_count_term_and_status_is_extracted_from_data_not_fixed_answers():
 a=inventory([read(SQL)])
 facts=[f for f in a['facts'] if f['kind']=='count_term']
 assert [f['subject'] for f in facts]==['alpha','beta']
 assert facts[0]['anchors']==['alpha','WAIT','RETRY']
 b=inventory([read(SQL.replace('alpha','other').replace('RETRY','PAUSED'))])
 assert any(f['anchors']==['other','WAIT','PAUSED'] for f in b['facts'])
 assert a['digest']!=b['digest']


def test_insert_column_value_pairs_keep_nested_json_cast_and_quoted_commas():
 source='''@Insert("""
INSERT INTO event (id, kind, payload) VALUES (#{id}, 'A,B', CAST(#{payload} AS JSON))
""")'''
 v=inventory([read(source)]);assert not v['unsupported']
 facts=[f for f in v['facts'] if f['kind']=='insert_field']
 assert [f['subject'] for f in facts]==['event.id','event.kind','event.payload']
 assert 'CAST' in facts[-1]['anchors'] and 'JSON' in facts[-1]['anchors']
 assert all(f['source_quote'] in source for f in facts)


@pytest.mark.parametrize('text,reason',[
 ('@Insert("""INSERT INTO x (a,b) VALUES (1)""")','insert_arity'),
 ('@Select("SELECT x")','unsupported_annotation'),
 ('@Select("""SELECT x --comment\n FROM y""")','sql_comments_unsupported'),
 ('@Insert("""INSERT INTO x (a) VALUES (1)','incomplete_literal'),
 (r'\u002f\u002f throw new Error();','unsupported_java'),
])
def test_unsupported_or_incomplete_shapes_are_explicit_gaps(text,reason):
 v=inventory([read(text)]);assert any(x['reason']==reason for x in v['unsupported'])


def test_fake_statements_inside_comments_or_strings_do_not_become_java_facts():
 text='// throw new Fake();\nString s="throw new Fake()";\n/* if (fake) */\nif (n >= 7) { throw new Real("no"); }'
 v=inventory([read(text)])
 assert len([f for f in v['facts'] if f['kind']=='condition'])==1
 assert len([f for f in v['facts'] if f['kind']=='exception'])==1
 assert all('Fake' not in f['source_quote'] for f in v['facts'])


def test_payload_fields_return_and_threshold_are_separate_check_items():
 text='''final int LIMIT = 7;
String payload = """{"requestId":"%s","version":2}""";
return new Result(id,"WAIT",0);'''
 v=inventory([read(text)])
 assert {f['subject'] for f in v['facts'] if f['kind']=='payload_field'}=={'requestId','version'}
 assert any(f['kind']=='constant' for f in v['facts']) and any(f['kind']=='return_value' for f in v['facts'])


@pytest.mark.parametrize('change',[
 lambda r:r.update(text=r['text']+'x'),lambda r:r.update(truncated=True),lambda r:r.update(end_line=99),lambda r:r.update(start_line=True),
])
def test_changed_truncated_or_misbound_source_is_not_inventory(change):
 r=read(SQL);change(r)
 with pytest.raises(ValueError):inventory([r])


def output(text='alpha WAIT RETRY',ids=None):return dict(title='Test',blocks=[dict(text=text,evidence_ids=['e1'] if ids is None else ids)])


def test_citations_alone_do_not_equal_explanation_and_literal_matches_never_approve():
 idx=inventory([read(SQL)])
 missing=scan(output('只做一次计数。'),idx)
 assert all(f['state']=='needs_review_missing_literal_anchors' for f in missing['facts'])
 found=scan(output('alpha WAIT RETRY means no count ever runs.'),idx)
 assert any(f['state']=='literal_anchors_found_semantics_unverified' for f in found['facts'])
 assert found['publication_approval'] is found['semantic_verified'] is False
 assert all(f['state']=='uncited' for f in scan(output(ids=[]),idx)['facts'])


def test_paraphrase_is_pending_human_judgement_not_auto_failed():
 idx=inventory([read(SQL)])
 r=scan(output('甲表等待和重试数加乙表等待数。'),idx)
 assert not r['publication_approval'] and 'paraphrases' in r['notice']
 assert not any('verdict' in f for f in r['facts'])


def test_plan_id_coverage_without_actual_quote_or_with_foreign_binding_is_rejected():
 idx=inventory([read(SQL)]);o=output()
 p=dict(inventory_digest=idx['digest'],output_digest=digest(o),items=[dict(fact_id=idx['facts'][0]['id'],block=1,quote='')])
 with pytest.raises(ValueError,match='plan_quote'):check_plan(o,idx,p)
 p['output_digest']='wrong'
 with pytest.raises(ValueError,match='plan_binding'):check_plan(o,idx,p)


def test_even_all_ids_with_existing_quotes_cannot_prove_semantic_coverage():
 idx=inventory([read(SQL)]);o=output('This explanation omits the SQL terms.')
 p=dict(inventory_digest=idx['digest'],output_digest=digest(o),items=[dict(fact_id=f['id'],block=1,quote=o['blocks'][0]['text']) for f in idx['facts']])
 r=check_plan(o,idx,p)
 assert not r['unassigned_facts'] and not r['publication_approval'] and not r['semantic_verified']
 assert all(x['status']=='quoted_but_semantic_judgement_required' for x in r['assigned'])
 p['items'].append(p['items'][0])
 with pytest.raises(ValueError,match='plan_fact'):check_plan(o,idx,p)

@pytest.mark.parametrize('path',['../Hidden.java','C:/Private.java','src/.git/Hidden.java','docs/M2-NEXT-CHAT-HANDOFF.md','.smtp.qq.local'])
def test_nonjava_protected_or_unsafe_paths_cannot_enter_the_syntax_inventory(path):
 r=read(SQL);r['path']=path
 with pytest.raises(ValueError,match='source_path'):inventory([r])

@pytest.mark.parametrize('change',[lambda r:r.update(path=None),lambda r:r.update(end_line=True)])
def test_malformed_archive_metadata_is_not_silently_coerced(change):
 r=read('final int N=1;');change(r)
 with pytest.raises(ValueError):inventory([r])


@pytest.mark.parametrize("payload",['{"x":NaN}','{"x":Infinity}','{"x":-Infinity}'])
def test_nonfinite_payload_literals_become_gaps_not_model_json(payload):
 v=inventory([read('String s="""'+payload+'""";')])
 assert not v["facts"] and any(x["reason"]=="unsupported_json_literal" for x in v["unsupported"])
