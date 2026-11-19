# Local assistant on an 8 GiB RTX 4060 laptop

This opt-in addition supplies a conservative Flash-Next launch profile and a single-worker task queue.
It does not change the upstream engine, CUDA kernels, routing, quantization, or defaults.

## Hardware profile

`rtx4060-64gb.json` targets a Ryzen 7 7735HS (8 physical cores), RTX 4060 Laptop (8 GiB),
and 64 GiB DDR5-4800. The candidate is the original ISTA-DASLab Flash-Next IQ2_XS:
32K context, one request, text only, seven CPU workers, 8 GiB requested RAM headroom,
and 768 MiB requested VRAM reserve. Headroom is an engine policy, not an OS memory limit.

These are **starting values, not measured performance improvements**. The launch plan
was accepted by Terminal 0.2.16 / engine 0.1.62. Flash weights were still downloading
when this profile was added; no Flash inference or tuning result is claimed.

Use an installed Terminal. For example, in PowerShell from the repository:

```powershell
.\local-assistant\start.ps1 -StrataCommand 'C:\path\to\bin\strata.cmd' -DataDirectory 'D:\LLM Models\Strata' -Plan
```

Inspect the plan, then omit `-Plan` to download and start. Add `-ModelPath` to reuse
the compatible GGUF files, or `-Tune` for the upstream quick tuner. Starting a different
already-running model is not forced: stop that model explicitly first. Compare stock
and tuned results on the same files, context, prompt, reasoning mode, and drafter.

## Bounded background work

Python 3.10 or newer, standard library only. Submit a UTF-8 text file:

```powershell
python local-assistant/worker.py --state D:/assistant-jobs submit --input D:/tasks/task.txt
python local-assistant/worker.py --state D:/assistant-jobs work --url http://127.0.0.1:8091/v1 --model strata
python local-assistant/worker.py --state D:/assistant-jobs status
```

`work` drains pending jobs and exits. It can run in a background terminal/process.
On Windows, an unattended `Start-Process` invocation should use `-WindowStyle Hidden`
and redirect stdout/stderr. No scheduled task or login service is installed.

One OS lock permits one worker per queue. SQLite records state; JSON results are
written atomically as `<job-id>.json`. A crashed running job becomes `interrupted`
on the next worker start and is not silently replayed. To retry, submit a new job.

Defaults: 2,048 output tokens, 180 seconds of wall time, no thinking, and at most
24,000 UTF-8 input bytes. Submission options: `--max-tokens` (up to 4,096),
`--deadline` (up to 900 seconds), and `--reasoning none|low`. The byte cap is a
conservative input bound, not a tokenizer measurement. Larger inputs need explicit splitting.

The endpoint must be localhost HTTP `/v1`. Redirects and HTTP proxies are disabled
for local calls. An optional key is read from `LOCAL_ASSISTANT_API_KEY`, never saved
in job results. Model-generated commands and tool calls are returned as data and
never executed. A `completed` job means the request finished; it is **not a factual
validation** of the answer. A human or supervising agent must review the result.

The timeout kills the request subprocess and closes its connection. Cancellation
on the server depends on the server; this program cannot force an arbitrary server
to release a GPU. A token-limit hit or empty answer is `incomplete`, not success.

## LM Studio fallback

LM Studio 0.4.28 build 2 did not honor `chat_template_kwargs.enable_thinking=false`
on `/v1/chat/completions` in the local test. Its native API with `reasoning: "off"`
worked. Use the adapter to avoid comparing thinking with non-thinking accidentally:

```powershell
python local-assistant/worker.py --state D:/assistant-jobs work --url http://127.0.0.1:8123/v1 --model local-assistant --protocol lmstudio
```

The LM Studio server/model must already be running. This adapter uses `/api/v1/chat`,
does not store chats, and maps `none` to `off`, `low` to `on`. LM Studio's native
response lacks `finish_reason`, so reaching the output-token cap is conservatively
classified as incomplete. Its `on` mode has no enforced separate reasoning budget.

## Validation

```powershell
python -m unittest discover -s local-assistant -p test_worker.py -v
```

Four tests passed on Windows: local HTTP generation and truncation, deadline and
crash recovery, exclusive queue ownership and input/endpoint limits, and native
LM Studio reasoning control. A real Qwen3.6 Q4_K_M task through the native adapter
completed in 9.54 seconds (284 output tokens, engine-reported 36.85 tokens/s).
Its summary contained an overstatement about Strata's stability, reinforcing the
need for review. This is an integration check, not a model quality benchmark.

No Flash speedup, CUDA modification, or Basalt benchmark has been measured here.
