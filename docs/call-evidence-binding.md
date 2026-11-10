# Host-bound call evidence and assertion review

This is a tested offline component, not enabled in the adapter, engine, task
storage or publisher. It performs no I/O, source fetching, model requests,
execution, response repair or publication. Existing source/session freshness,
cancellation, cost, context/output and native citation guards remain required.

## Trust boundary

`HostScope` and its fixed file seals must come from the host's independently
verified source session/policy. Accepted references must come from the current
engine's accepted-read set; chunks from that task's actual saved feedback. These
are trusted host inputs, never model-authored JSON. This component does not prove
an arbitrary Python caller is trusted or authorize a new source session.

`bind` checks task/project identity, repository/commit, permitted Java paths,
actual read spans within host ranges, non-truncation, exact excerpt text, SHA256
and Git blob SHA1. It uses the existing tool's LF/CRLF line rules. Repeated or
unread evidence, altered bytes/chunks, wrong task/target or manual context flags
are rejected with bounded diagnostics.

Already-readable full immutable blobs provide lexical state, comments/string/
text-block masking and delimiter pairs. Extraction filters before constructing
labels: only complete call spans inside each actual accepted read are exported.
No outside call names/bodies or extra callee implementation are supplied to the
model. The same full blobs were already read for source integrity; this grants
no additional model-visible source range. Unsupported lexical contexts yield
unavailable syntax, never guessed positive evidence. No Java type/overload/
runtime resolver is claimed.

`BoundIndex` is immutable process-local host data minted by the factory, not a
client-verifiable signature or sandbox against trusted Python code. A copied
JSON view cannot serve as authority. Distinct same-line call sites retain
different IDs. Identifiers of unknown type stay unknown; absence of an arrow
does not prove absence of a callable.

## Model data and source-call sidecar

`model_view` is an all-or-nothing bounded rows representation with fixed columns
and an explicit evidence-ID table/index base. It preserves every extracted call.
The host must budget it with the entire request and stop before dispatch if it
does not fit. No silent truncation or context-limit increase exists. The real
regression's 48 calls take 4555 compact characters versus 8520 in the initial
dictionary form. Adding 4555 to that old run's recorded maximum context gives
20258, below 24000. This is a static sizing comparison, not a new live request.

The model/reviewer proposes a call ID, field/block location, exact quote and
assertion kind. The host binds the quote only when it occurs exactly once, and
computes current output/index digests and Unicode-codepoint offsets itself.
No model hash computation or offset guessing is required. Altered output,
replayed digests, incorrect locations, overlaps, uncited/unknown calls or forged
host fields stop review. Identity sealing is not truth certification.

`invoke` supports only existence of that syntactic invocation; `pass_callable`
requires explicit lambda/method-reference syntax in that invocation's arguments.
Registration or execution requires receiver evidence and remains unproved here.
Relabelling callback prose as `invoke` does not launder it: quote checks, coverage
and an independent bound-body audit also run.

Every result has `semantic_review_required=true`, `semantic_verified=false`,
and `publication_approval=false`. Even `syntax_supported_only` is not semantic
acceptance or permission to publish. The finite language scanner is incomplete
for arbitrary paraphrases/cross-clause semantics. A complete section still needs
independent source-assisted acceptance against all original criteria.

## Validation and remaining integration

237 checks pass: 62 new binding/claim tests and 175 existing call/learning/
protocol/range/named-adapter/follow-up-binding checks. Automatic replay uses the
original current-task accepted events, five actual persisted evidence chunks,
and fixed blobs: all 211 admitted lines, no manually picked window/context flag.
It finds the original block-3 registration and block-6 passing errors, rejects
unsupported typed assertions and missing sidecars, and leaves original score
5/6, all original artifact hashes and database rows unchanged.

Next integrate a host-owned accepted-read collector and frozen candidate/review
lifecycle, using this factory and existing source-session guards. Test held
unsupported candidates, unchanged originals, stale source/cancellation, budget
overflow and storage/API compatibility before any new paid intent. Do not
expose authority constructors to model tools, infer registration from a name,
inject frozen answers, or bypass independent semantic review.
