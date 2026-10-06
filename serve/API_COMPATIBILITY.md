# API compatibility

Strata-T8 keeps protocol 1, lazy vision loading, cancellation, process ownership,
and the ComfyUI managed-service contract when syncing upstream releases.

Structured output requires exactly one JSON object by default. For clients that
need upstream's extraction of an object from prose or a Markdown fence, set
`"strata_json_compatibility": true` in a Chat Completions or Responses request.
Schema validation, duplicate-key checks, nonfinite-number checks and incomplete
generation errors still apply. Invalid output returns `structured_output_failed`;
the server never automatically regenerates an answer.

Chat Completions and Anthropic Messages reject malformed tool history by default.
Set `"strata_history_compatibility": true` to preserve a truncated argument string
or a non-object argument as text under `arguments`. This option does not allow
duplicate JSON keys, nonfinite numbers, invalid Unicode or excessive nesting.
Both options default to `false` and must be JSON booleans.

Tool-call XML inside Markdown examples stays text. Namespaced Responses tools also
accept the model's `namespace__name` spelling, with the original parameter schema.
Real tool names take priority; ambiguous aliases are disabled.

Session save/restore requires `slot_save_path` (or `--slot-save-path`) and the
matching native engine. It is disabled by default. `reasoning_loop_recovery` also
stays disabled unless configured explicitly.
