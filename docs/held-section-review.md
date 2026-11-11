# Opt-in held section candidates

This component integrates host-owned accepted-read collection and an atomic
hold in the actual TaskService. The newer optional host review/release and live
model projection are described in independent-section-publication.md. A hold
alone still never approves semantics or publication. No new paid entry is
authorized by these components. Default runtime is unchanged.

The host may provide a frozen `ReviewPolicy` through the Python `create_app` or
TaskService constructor. HTTP/model selection cannot set it. Policy binds the
repository, commit, topic, section, fixed file hashes and admitted spans. A
mismatch stops before section task creation. All original source-session,
permission, model-purpose, budget, cancellation and persistence guards remain.

For enabled section tasks, read_excerpt is executed with the existing SourceTools
against the current verified source. A temporary verified-read observer captures
only those already-read bytes; it adds no source requests, paths or model-visible
lines. It runs through the original tool instance, preserving ranged subclasses,
their masked searches and custom digest checks, and restores earlier observers.
Existing secret/encoding/size filters still run. SHA256 and Git blob identity
must match the fixed policy before feedback returns. Other tools are unchanged.
The collector matches actual committed saved feedback and only accepts reads
after the current engine emits a successfully persisted tool_feedback_accepted
event. Raw successful returns, old database rows and final citations cannot
stand in for these events. Final evidence must exactly match the accepted set.

After engine validation, the current host binds the source index, original output
and bounded call audit into an immutable FrozenCandidate. Even no findings yields
needs_review. Omitted sidecars remain unreviewed. There is no approval inference
from invocation syntax or names, no answer repair, and no semantic_verified=true.
The 128000-byte candidate cap is additional storage, not a larger source/context
or output budget; excess stops without truncation. Deadline, cancellation and
source-session checks run again before saving.

The terminal task result and one append-only held_section_candidates row commit
in one transaction. Save failure rolls both back; the task subsequently fails
without publishing. A validated original remains in the failed task for diagnosis
when freezing/storage fails, with quality unverified and no learning record;
cancellation/version/session races still follow the original stop rules.
The held task says needs_review, the frontend labels it as an
unpublished candidate, and LearningStore is not invoked. Cancelled/version-
changed tasks cannot resurrect or release. No approval/release endpoint exists.
The candidate table is created only by explicit host opt-in. Old/default and
reopened services can read held summaries without enabling generation or
reconstituting process-local source authority. Project deletion cascades only
through owned assistant tables.

Tests use scripted models, temporary SQLite and local Git plus mocked public
metadata. They establish pipeline boundaries, not production model quality or
all original semantic criteria. Original S03 replay uses all five real accepted
reads/211 lines, keeps the old body and 5/6 grade intact, and demonstrates that
the two unsupported assertions would be held. It does not retract the historical
published record. One read per accepted excerpt is captured; no extra receiver
implementation or source range is acquired.

The native-section and two existing follow-up manifests include the changed
host/storage path and all call-evidence modules. Drift in these inputs must stop
before request/reservation. Original tracked CRLF files retain their line endings.
The independent review contract now supplies that separate optional host path.
Do not use an empty audit as publication approval or deserialize a saved index
as fresh authorization; see the newer document for trust and lifecycle limits.
