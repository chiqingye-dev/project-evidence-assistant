# Offline call evidence review prototype

`call_evidence.py` is a data-only experiment, not enabled in the adapter, task
service or publication path. It performs no I/O, model calls, source fetching,
Java execution, database writes or response repair. `within_prototype_scope`
never means semantic acceptance or publication approval; `publication_approval`
and `semantic_verified` remain false for every outcome.

The original low-level `Window` interface remains for bounded syntax fixtures.
Production callers must not treat its manually supplied context flag as proof.
The separate host-only `call_binding.py` now binds engine-accepted reads and
exact chunks to source-session blob seals, derives lexical context automatically,
and exports only calls wholly inside those reads. Its opaque result cannot be
rehydrated from model JSON. See `call-evidence-binding.md`. The original manual
replay is historical evidence; the new replay binds all five actual reads.

The bounded Java tokenizer masks comments, strings and text blocks; rejects
incomplete/unsupported forms; and distinguishes an explicit lambda/method
reference in a call's own arguments from one nested in a different invocation.
An identifier argument stays unknown: it could hold a callable. No negative
callback-type conclusion follows from missing explicit syntax. Complex angle
forms remain unsupported, and no Java overload/type/receiver resolution is
claimed. Qualified source receivers cannot be borrowed by a different target.

The review scans section title, block headings/text (including Mermaid) and
gaps. It flags unproved passing/registration language, distinguishes bounded
uncertainty phrases from assertions, and does not let a later caveat retract
an earlier assertion. Registration and execution semantics remain unverified
without receiver evidence. The Chinese/English trigger and uncertainty grammar
is intentionally finite: arbitrary paraphrases, cross-clause relationships,
unusual Markdown and all semantic facts are NOT comprehensively validated.
Conservative review findings are not proofs that a claim is false.

Before publication integration, wire the host-owned binding/claim review to the
actual accepted-read and candidate lifecycle, preserve source/cancel/budget guards,
and implement held-candidate/independent review behavior. The new claim contract
binds source syntax, not arbitrary prose truth. Do not use the phrase scanner or
supported sidecars alone as permission to publish or proof of all six S03 criteria.
Do not expand repository scope or inject frozen evaluation answers.

The original S03 body and native records remain untouched at source-assisted
5/6. Final regression replay flags block 3's registration assertion and block
6's passed-callback assertion, while retaining the uncertainty/gap statements.
An initial gap false positive and its original implementation/report are kept
in private evidence. Final targeted checks: 47 new boundaries plus 47 existing
learning/protocol checks, 94 total. These are offline checks, not model trials,
runtime Java verification or personal mastery.
