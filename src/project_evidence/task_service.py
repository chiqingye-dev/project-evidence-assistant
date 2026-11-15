"""Single bounded worker and persistent task publication with source guards."""

from __future__ import annotations

from contextlib import contextmanager
import sqlite3
from threading import Event, RLock, Thread
import time

from .agent_engine import run_agent
from .agent_protocol import (AgentBudget, ModelContext, UnconfiguredAdapter, DirectoryOutput,
                             GOAL, OUTLINE_GOAL, SECTION_GOAL, FOLLOW_UP_GOAL)
from .learning_contract import (OutlineSelection, SectionSelection, FollowUpSelection,
                                LearningAnchor, ChapterOutline, SectionExplanation)
from .learning_store import LearningStore
from .source_policy import require_allowed_path
from .source import SourceError
from .source_service import SourceService
from .storage import ProjectStore
from .task_storage import ACTIVE, MAX_TASK_RECORDS, TaskStore
from .task_diagnostics import engine_metadata
from .generation_diagnostics import freeze_check
from .section_review import ReviewPolicy, AcceptedReads, HeldReviewStore
from .section_semantic_review import ReviewContract,HostReviewer,ReviewStore,assess


class TaskService:
    def __init__(self, projects: ProjectStore, sources: SourceService, adapter=None, *, section_review_policy=None, review_contract=None) -> None:
        self.projects, self.sources = projects, sources
        self.history = TaskStore(projects)
        if section_review_policy is not None and type(section_review_policy) is not ReviewPolicy:
            raise ValueError('section_review_policy')
        self.section_review_policy=section_review_policy
        if section_review_policy is not None:
            facets=section_review_policy.coverage_facets
            from .source_coverage import FAMILIES
            if (type(facets) is not tuple or any(type(f) is not str or f not in FAMILIES for f in facets)
                    or len(set(facets))!=len(facets)):
                raise ValueError('host_coverage_facets')
            objectives=section_review_policy.public_objectives
            layout=section_review_policy.generation_layout
            if layout not in ('rows_v1','preloaded_groups_v1') or (layout!='rows_v1' and not objectives):
                raise ValueError('generation_layout')
            if type(objectives) is not tuple:
                raise ValueError('public_objectives')
            if objectives:
                from .generation_contract import PublicBrief
                PublicBrief(objectives)
                if not facets or type(review_contract) is not ReviewContract:
                    raise ValueError('generation_review_policy')
                if getattr(adapter,'kind',None)=='real' and getattr(adapter,'section_protocol',None)!='named_functions':
                    raise ValueError('generation_native_protocol')
        self.held_reviews=HeldReviewStore(projects,create=section_review_policy is not None)
        if review_contract is not None and (type(review_contract) is not ReviewContract or section_review_policy is None):
            raise ValueError('review_contract')
        if review_contract is not None:
            from .source_policy import normalize_github_repository
            if (review_contract.commit!=section_review_policy.commit or
                    normalize_github_repository(review_contract.public_url)!=normalize_github_repository(section_review_policy.public_url)):
                raise ValueError('review_contract')
        self.review_contract=review_contract
        self.independent_reviews=ReviewStore(projects,create=review_contract is not None,contract=review_contract)
        self._held_live={}
        self._review_inflight=set()
        self._review_clock=time.monotonic
        self.learning = LearningStore(projects)
        self.adapter = adapter if adapter is not None else UnconfiguredAdapter()
        self.limits = AgentBudget(max_retries=0) if (section_review_policy is not None and
            section_review_policy.public_objectives) else AgentBudget()
        self._lock = RLock()
        self._thread: Thread | None = None
        self._cancel = Event()
        self._active_project: str | None = None
        self._active_task: str | None = None

    def _display(self, record, task: dict) -> dict:
        value=dict(task, review_status="current" if task["commit"] == record.commit else "needs_review",
                   source_verified=self.sources.active(record))
        held=self.held_reviews.summary(record.id,task['id'])
        if held is not None:value['candidate_review']=held
        reviewed=self.independent_reviews.summary(record.id,task['id'])
        if reviewed is not None:value['independent_review']=reviewed
        return value

    def list(self, record, *, limit=100, offset=0) -> list[dict]:
        return [self._display(record, task) for task in self.history.list(record.id, limit=limit, offset=offset)]

    def get(self, record, task_id) -> dict | None:
        task = self.history.get(record.id, task_id)
        return self._display(record, task) if task is not None else None

    @contextmanager
    def project_change(self, project_id):
        """Serialize version/delete with start and final publication."""
        with self._lock:
            for task_id,item in list(self._held_live.items()):
                if str(item[1].view()['project_id'])==str(project_id):self._held_live.pop(task_id,None)
            if str(project_id) == self._active_project:
                self._cancel.set()
            self.history.stop_project(project_id, "source_changed", "选定来源变化，任务停止；旧记录保留。")
            yield

    def cancel(self, record, task_id) -> dict | None:
        with self._lock:
            task = self.history.get(record.id, task_id)
            if task is None:
                return None
            if task["status"] in ACTIVE:
                if str(task_id) == self._active_task:
                    self._cancel.set()
                self.history.update(record.id, task_id, status="cancelled",
                                    message="已取消；在途调用返回前不会启动下一步，已保存证据仍保留。")
            elif task['status']=='needs_review':
                self.independent_reviews.cancel(self.history,record.id,task_id)
                self._held_live.pop(str(task_id),None)
            return self.get(record, task_id)

    def shutdown(self) -> None:
        # No broad process termination. Adapter/tool in-flight limits are cooperative.
        with self._lock:
            self._cancel.set()
            self._held_live.clear()
            if self._active_project:
                self.history.stop_project(self._active_project, "interrupted", "后台停止，任务中断；不会自动重试。")

    def query_enabled(self):
        return self.topic_enabled("query")

    def topic_enabled(self, topic):
        return (self.adapter.is_configured and (self.adapter.execution_mode == "scripted"
                or topic in getattr(self.adapter, "allowed_topics", ())))

    def followup_enabled(self):
        return self.adapter.is_configured and (self.adapter.execution_mode == "scripted"
                                               or getattr(self.adapter, "enable_followup", False))

    def _anchor(self, record, kind, inputs):
        section = parent = None
        followup_fields = {}
        if kind in {SECTION_GOAL, FOLLOW_UP_GOAL}:
            if kind == FOLLOW_UP_GOAL:
                followup = FollowUpSelection.model_validate(inputs)
                body = self.learning.get(record, followup.body_id)
                if (body is None or body["kind"] != SECTION_GOAL or body["review_status"] != "current"
                        or body["execution_mode"] != self.adapter.execution_mode):
                    raise ValueError("invalid_followup_body")
                explanation = SectionExplanation.model_validate(body["content"])
                previous = "\n".join([explanation.title, *[block.heading + "：" + block.text for block in explanation.blocks],
                                       *["缺口：" + gap for gap in explanation.gaps]])
                followup_fields = {"body_id": body["id"], "question": followup.question,
                                   "previous_excerpt": previous[:3000], "previous_excerpt_truncated": len(previous) > 3000}
                selection = SectionSelection(outline_id=body["parent_outline_id"], section_id=explanation.section_id)
            else:
                selection = SectionSelection.model_validate(inputs)
            parent = self.learning.get(record, selection.outline_id)
            if (parent is None or parent["kind"] != OUTLINE_GOAL or parent["review_status"] != "current"
                    or parent["execution_mode"] != self.adapter.execution_mode):
                raise ValueError("invalid_learning_parent")
            outline = ChapterOutline.model_validate(parent["content"])
            if kind == FOLLOW_UP_GOAL and explanation.topic != outline.topic:
                raise ValueError("invalid_followup_topic")
            section = next((item for item in outline.sections if item.id == selection.section_id), None)
            if section is None:
                raise ValueError("invalid_learning_section")
            selection = OutlineSelection(directory_task_id=parent["directory_task_id"], chapter_index=parent["chapter_index"], topic=outline.topic)
        else:
            selection = OutlineSelection.model_validate(inputs)
        directory = self.history.get(record.id, selection.directory_task_id)
        if directory is None:
            raise ValueError("invalid_learning_directory")
        LearningStore._identity(directory, record, kind="directory", mode=self.adapter.execution_mode)
        tree = DirectoryOutput.model_validate(directory["result"])
        if selection.chapter_index >= len(tree.chapters):
            raise ValueError("invalid_learning_chapter")
        chapter = tree.chapters[selection.chapter_index]
        if chapter.kind != "backend":
            raise ValueError("backend_chapter_required")
        if selection.topic == "query" and "查询" not in chapter.title and "详情" not in chapter.title:
            raise ValueError("query_chapter_required")
        hints = []
        for entry in (section.entry_hints if section else chapter.entries):
            # Directory entries can also be method names, not exact file paths.
            if entry.endswith(".java") and "/" in entry:
                if section is None:
                    tokens = {"submission": ("submission",), "consumption": ("outbox", "listener", "rabbit"),
                              "judging": ("judgetaskrunner", "sandbox", "snapshot", "completion")}
                    if selection.topic in tokens and not any(token in entry.lower() for token in tokens[selection.topic]):
                        continue
                require_allowed_path(entry); hints.append(entry)
        return LearningAnchor(directory_task_id=directory["id"], chapter_index=selection.chapter_index,
            topic=selection.topic,
            title=chapter.title if selection.topic == "query" else {"submission": "提交链路（待查证）", "consumption": "消息消费链路（待查证）", "judging": "判题执行链路（待查证）"}[selection.topic],
            summary=chapter.summary if selection.topic == "query" else "从所选后端目录继续查证所选主题；目录和入口线索不充当已核对事实。",
            entry_hints=hints,
            outline_id=parent["id"] if parent else None, section_id=section.id if section else None,
            section_title=section.title if section else None, section_objective=section.objective if section else None,
            **followup_fields)

    def start(self, record, *, kind="directory", inputs=None) -> dict:
        with self._lock:
            # Default path never opens source, stores a fake task, or calls a model.
            if not self.adapter.is_configured:
                return {"status": "model_not_configured", "message": "模型尚未配置，未启动调用，也未生成学习目录。当前没有模型费用。"}
            if self._thread is not None and self._thread.is_alive():
                return {"status": "task_busy", "message": "已有任务或取消后的在途调用；请等待它结束再开始。"}
            if kind==SECTION_GOAL and self.section_review_policy is not None and len(self._held_live)>=8:
                return {'status':'review_capacity','message':'本机待审候选已达上限；请先复核或取消，不会启动新模型任务。'}
            goal = GOAL if kind == "directory" else kind
            if goal not in {GOAL, OUTLINE_GOAL, SECTION_GOAL, FOLLOW_UP_GOAL}:
                raise ValueError("invalid_task_kind")
            if goal == FOLLOW_UP_GOAL and not self.followup_enabled():
                return {"status": "followup_model_not_enabled", "message": "追问付费用途尚未启用；没有启动任务或模型请求。"}
            try:
                if goal == OUTLINE_GOAL:
                    requested = OutlineSelection.model_validate(inputs)
                    if not self.topic_enabled(requested.topic):
                        return {"status": f"{requested.topic}_model_not_enabled",
                                "message": "所选主题的付费用途尚未启用；没有启动任务或模型请求。"}
                anchor = self._anchor(record, kind, inputs) if goal != GOAL else None
                if anchor is not None and not self.topic_enabled(anchor.topic):
                    return {"status": f"{anchor.topic}_model_not_enabled",
                            "message": "所选主题的付费用途尚未启用；没有启动任务或模型请求。"}
                context = ModelContext(commit=record.commit, public_url=record.public_url, goal=goal, anchor=anchor)
                if goal==SECTION_GOAL and self.section_review_policy is not None:
                    self.section_review_policy.scope(record,'pending',anchor)
            except ValueError:
                return {"status": "invalid_selection", "message": "所选目录、查询章节或小节不属于当前有效版本；没有启动任务。"}
            identity = self.sources.session_identity(record)
            self.sources.tools(record)  # Confirm source access before task creation.
            try:
                task = self.history.create(record, self.adapter.execution_mode, self.limits.model_dump(), kind=kind, inputs=inputs)
            except ValueError as error:
                if str(error) == "task_payload_limit":
                    return {"status": "limit_exceeded", "message": "任务初始记录超过大小上限；未创建任务或调用模型。"}
                code = "limit_exceeded" if str(error) == "task_limit" else "source_changed"
                return {"status": code, "message": f"任务历史达到{MAX_TASK_RECORDS}条存储上限，原有记录仍保留。" if code == "limit_exceeded" else "来源变化，未启动任务。"}
            self._cancel = Event()
            self._active_project, self._active_task = str(record.id), task["id"]
            cancellation = self._cancel
            self._thread = Thread(target=self._run, args=(record, task["id"], identity, cancellation, context),
                                  name="project-evidence-agent", daemon=True)
            self._thread.start()
            return self._display(record, task)

    def _run(self, record, task_id: str, identity, cancellation: Event, context: ModelContext) -> None:
        started=time.monotonic()
        collector=None
        payload=None
        service_step='service_guard'
        def failed_candidate():
            # Retain a structurally validated original in the failed task when
            # freezing/storage fails. It is not a learning record or approval.
            return payload.get('final_output') if collector is not None and payload is not None and payload.get('status')=='completed' else None
        def cancelled() -> bool:
            task = self.history.get(record.id, task_id)
            return cancellation.is_set() or task is None or task["status"] not in ACTIVE

        def guard() -> None:
            with self._lock:
                latest = self.projects.get(record.id)
                if latest is None or latest.commit != record.commit:
                    raise SourceError("source_changed", "登记记录或选定版本变化，任务停止。")
                self.sources.require_session(latest, identity)

        def execute(name: str, arguments: dict) -> dict:
            guard()
            tools = self.sources.tools(record)
            methods = {"find_files": tools.find_files, "search_text": tools.search_text, "read_excerpt": tools.read_excerpt}
            result = (collector.read(tools,arguments) if collector is not None and name=='read_excerpt'
                      else methods[name](**arguments))
            guard()
            if name == "read_excerpt" and result.get("status") == "ok" and result.get("text"):
                with self._lock:
                    guard()
                    with self.sources.checked_session(record, identity):
                        try:
                            saved = self.projects.save_evidence(record.id, result)
                        except (sqlite3.IntegrityError, ValueError):
                            raise SourceError("budget_exhausted", "证据保存未完成或容量达到上限，任务停止。") from None
                        result["evidence_id"] = saved["id"]
                        if collector is not None:collector.saved(result,saved)
            return result

        def record_event(event: dict) -> None:
            details = event.get("details", {})
            safe = {"kind": event.get("type", "action"), "message": event.get("summary", "行动记录")}
            for key in ("tool", "status", "arguments", "truncated", "items_count", "evidence_id", "guidance_sha256", "plan_digest"):
                if key in details:
                    safe[key] = details[key]
            safe.update(engine_metadata(event))
            stored=self.history.update(record.id, task_id, event=safe, counters=event.get("counters", {}))
            self.history.record_counters(record.id, task_id, event.get("counters", {}))
            if collector is not None:
                if not stored:return False
                collector.accept(event)

        try:
            if not self.history.update(record.id, task_id, status="running", message="正在按固定公开版本查证；引用是否支持结论仍需验收。"):
                return
            if context.goal==SECTION_GOAL and self.section_review_policy is not None:
                guard()
                collector=AcceptedReads(self.section_review_policy.scope(record,task_id,context.anchor))
            # No local path, registration name, secrets, or historical unverified text.
            adapter = (self.adapter.for_task(task_id, goal=context.goal, topic=context.anchor.topic) if context.goal != GOAL
                       else self.adapter.for_task(task_id)) if hasattr(self.adapter, "for_task") else self.adapter
            protocol = getattr(adapter, 'followup_protocol', None)
            if context.goal == FOLLOW_UP_GOAL and protocol in {'strict_function', 'two_phase'}:
                self.history.update(record.id, task_id, event={"kind": "transport_selected",
                    "message": "追问传输：" + protocol + ("；规划格式：" + adapter.followup_claim_format if getattr(adapter, "followup_claim_format", "literal") in ("selected_parts_v1", "selected_table_v1") else "") + ("；正文格式：" + adapter.followup_prose_format if getattr(adapter, "followup_prose_format", "indexed") in ("kind_prose_v1", "flat_prose_v1") else "") + ("；位置视图：" + adapter.followup_scope_view if getattr(adapter, "followup_scope_view", "off") in ("java_scope_edge_view_v1", "java_scope_edge_unit_columns_v1", "java_scope_edge_unit_index_columns_v1") else "") + "；只接受固定动作载体，仍执行原引用校验。"})
            host_context=None
            generation=None
            if collector is not None:
                facets=self.section_review_policy.coverage_facets
                host_context=(lambda refs:collector.coverage_context(refs,facets)) if facets else collector.model_context
                if self.section_review_policy.public_objectives:
                    from .generation_loop import GenerationSession
                    generation=GenerationSession(collector,facets,self.review_contract,
                                                 self.section_review_policy.public_objectives,
                                                 layout=self.section_review_policy.generation_layout)
            outcome = run_agent(adapter, execute, guard, cancelled, self.limits,
                                record_event, context,host_context=host_context,section_generation=generation)
            payload = outcome.model_dump(mode="json")
            # If the event callback failed, retain a finite fallback in the final
            # CAS transaction. No model/event callback retry or terminal revival.
            recovery=None
            for event in reversed(payload['events']):
                safe=engine_metadata(event)
                if safe.get('generation_check')==dict(step='event_persist',reason='event_not_saved'):
                    recovery=dict(kind='generation_rejected',message='行动记录未保存；终态事务保留固定诊断。',**safe)
                    break
            service_step='terminal_store'
            self.history.record_counters(record.id, task_id, payload["counters"], payload.get("evidence", []))
            status = {"clarify": "needs_clarification", "not_configured": "failed"}.get(payload["status"], payload["status"])
            with self._lock:
                if not cancelled():
                    service_step='service_guard'
                    guard()
                    with self.sources.checked_session(record, identity):
                        held=None
                        if collector is not None and status=='completed':
                            service_step='hold_freeze'
                            held=(generation.freeze(payload['final_output'],payload['evidence']) if generation is not None
                                  else collector.freeze(payload['final_output'],payload['evidence']))
                            if generation is not None:service_step='freeze_post_guard'
                            guard()
                            if cancelled():return
                            if time.monotonic()-started>=self.limits.max_seconds:
                                raise SourceError('timeout','冻结复核超过任务时间上限。')
                            status='needs_review'
                            payload['message']='正文候选已冻结待源码复核；未发布学习正文，不能将句法检查当成语义通过。'
                        service_step='terminal_store'
                        stored=self.history.update(record.id, task_id, status=status, message=payload["message"],event=recovery,
                                            counters=payload["counters"], result=payload.get("final_output"),
                                            evidence=payload.get("evidence", []),
                                            publish=(lambda connection: self.learning.save_from_task(record.id, task_id, transaction=connection))
                                            if status == "completed" and context.goal != GOAL else None,
                                            hold=(lambda connection:self.held_reviews.save(connection,held)) if held is not None else None)
                        if stored and held is not None:self._held_live[task_id]=(collector,held,identity)
        except SourceError as error:
            with self._lock:
                failed_step=getattr(generation,'freeze_step','hold_freeze') if service_step=='hold_freeze' else service_step
                status = error.code if error.code in {"source_changed", "unverified", "denied", "budget_exhausted", "timeout"} else "failed"
                self.history.update(record.id, task_id, status=status, message="来源许可、版本或证据保存门禁使任务停止；没有发布新内容。",
                                    event=dict(kind='generation_rejected',message='源码或保存阶段停止。',generation_check=dict(step=failed_step,reason='source_error')) if collector is not None else None,
                                    result=failed_candidate() if status in {'budget_exhausted','timeout'} else None)
        except Exception as error:
            # Never persist raw adapter/SQL/network errors: they can contain secrets.
            check=(freeze_check(getattr(generation,'freeze_step','hold_freeze'),error)
                   if service_step=='hold_freeze' else dict(step=service_step,reason='unexpected_exception'))
            self.history.update(record.id, task_id, status="failed", message="任务未完成，未保存异常原文；请检查配置和有限边界。",
                                event=dict(kind='generation_rejected',message='本地保存阶段失败；异常原文不入日志。',generation_check=check) if collector is not None else None,
                                result=failed_candidate())
        finally:
            with self._lock:
                if self._active_task == task_id:
                    self._active_project = self._active_task = None

    def review_held(self,record,task_id,reviewer):
        """Host Python operation only: never exposed to HTTP/model tools.

        Source-assisted reviewer judgement is explicit, not automated semantic
        proof. Disk views alone cannot recreate a live review authorization.
        """
        task_id=str(task_id);started=self._review_clock()
        with self._lock:
            if self.review_contract is None or type(reviewer) is not HostReviewer:
                raise ValueError('review_not_configured')
            live=self._held_live.get(task_id)
            if live is None or task_id in self._review_inflight:raise ValueError('review_not_live')
            collector,candidate,identity=live
            def guard_review():
                latest=self.projects.get(record.id);task=self.history.get(record.id,task_id)
                if (latest is None or latest.commit!=record.commit or self._held_live.get(task_id) is not live
                        or task is None or task['status']!='needs_review'
                        or str(record.id)!=candidate.view()['project_id']):
                    raise SourceError('source_changed','待审候选或选定版本已经变化。')
                self.sources.require_session(latest,identity)
                return task
            task=guard_review()
            self.independent_reviews.require_capacity(record.id,task_id)
            self._review_inflight.add(task_id)
        phase='review'
        try:
            checked=assess(candidate,collector,self.review_contract,reviewer,task['execution_mode'])
            with self._lock:
                guard_review()
                if self._review_clock()-started>=30:raise SourceError('timeout','独立复核超过本轮时限。')
                with self.sources.checked_session(record,identity):
                    phase='publication'
                    receipt=self.independent_reviews.finish(self.history,self.learning,candidate,checked)
                    self._held_live.pop(task_id,None)
            return receipt
        except Exception as error:
            reason=('review_timeout' if isinstance(error,SourceError) and error.code=='timeout' else
                    'review_state_changed' if isinstance(error,SourceError) else
                    'publication_failed' if phase=='publication' else 'invalid_review')
            # Fixed bounded diagnostics only, never callback/SQL exception text.
            try:self.independent_reviews.error(record.id,task_id,candidate,reason)
            except Exception:pass
            raise ValueError(reason) from None
        finally:
            with self._lock:self._review_inflight.discard(task_id)
