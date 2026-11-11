# Optional bounded section output

`DeepSeekAdapter(..., section_protocol="named_functions",
section_output_profile="compact_v1")` explicitly narrows generation for submission
sections. The default remains `off`; other protocols cannot enable this profile.
Task clones preserve it. No app setting, active server, budget or source permission
is enabled by this option.

The six native function fields retain their original meaning. With at least 2400
remaining output tokens, the publication schema allows at most six blocks, 320
characters per block text, 48 per heading, three original evidence IDs per block,
and three gaps of at most 100 characters. This is a subset of the original content
contract. The decoder checks those bounds locally because upstream `strict=False`
is advisory. It rejects oversized output without clipping, completing or repairing
the original model data. Original content, citation, source, cancellation, review
and atomic publication checks still apply.

The request uses the smaller of the caller allowance and `context.remaining`.
Below 2400 tokens, only `report_gap` and `request_clarification` are offered and
accepted; source reads and `publish_section` are unavailable. The shared 4000
output-token budget is not restarted after reads. Provider `finish_reason=length`
still stops immediately, settles a valid receipt and never triggers continuation
or a retry. The frozen base guidance remains checked as before; per-turn sizing
guidance is derived from this pinned code and the actual remaining allowance.
An eventual trial must explicitly bind this profile and its full request hash.

The profile gives priority to required facts, real references and unknowns. It
does not receive golden answers or examples. Repeated exposition, a diagram,
questions and exercises are optional when they would crowd out necessary facts.
If a complete supported section cannot fit, report a gap rather than omit facts.
This is not approval to relax the six original S03 criteria.

Local capacity evidence uses the tokenizer data linked by DeepSeek's official
[token usage documentation](https://api-docs.deepseek.com/zh-cn/quick_start/token_usage/).
The archived original function arguments count 2599 locally, versus a real final
completion receipt of 2649; this observed difference of 50 is not a guarantee for
future protocol framing. A separately authored six-criterion capacity example
counts 1275 argument tokens. With the actual historical 405 read-turn tokens and
a diagnostic 256-token framing reserve, the estimate is 1936 of 4000. Pretty JSON
and long Java-like syntax cases were also measured. The example is a manual
fixture, never a new model answer, prompt injection or published learning record.

The same character-bounded schema admits a rare-Unicode counterexample that
exceeds the token cap. Therefore neither character bounds nor the local tokenizer
prove that arbitrary model output will fit, and successful sizing does not prove
semantic quality. No local tokenizer package/data is added to runtime dependencies.
Actual provider receipts remain authoritative. The next new trial still needs
fresh resource gates, a new sealed entry and independent whole-output review.

Validation: 132 output/native/call-context tests and 73 review/publication tests,
with mock transport or temporary databases only. Historical failed results and
the first seal-order/test/download failures are preserved outside this candidate.
The active runtime has not been switched and no new paid entry was prepared.
