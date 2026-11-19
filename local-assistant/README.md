# Local assistant on an RTX 4060 laptop

This opt-in addition runs Flash-Next with a measured laptop profile and a bounded,
single-worker task queue. It does not change engine kernels, quantization, routing,
or upstream launch defaults. The profile retains automatic memory placement and
explicitly selects the drafter and vocabulary used in the measurements.

## Measured profile

Tested on 19 November 2026: Ryzen 7 7735HS, 8 cores / 16 threads, 64 GiB DDR5-4800,
RTX 4060 Laptop 8 GiB, Windows 11, AC power / Turbo, driver 617.42.
Terminal 0.2.16 and engine 0.1.62, native Windows.

`rtx4060-64gb.json` selects the full ISTA-DASLab Qwen3.8-Flash-Next GSQ-RCO IQ2_XS,
32K context, one request, text only, seven CPU pool workers, DFlash2, and Cyrillic
draft vocabulary. The automatic drafter was Q4_0-selq4, speculation length 6,
confidence 0.8. The host thread is additional to the seven pool workers.

| Flash configuration | Median decode tokens/s | Six-run range |
| --- | ---: | ---: |
| Seven workers, DFlash2 | 42.68 | 40.84–55.74 |
| Six workers, DFlash2 | 42.55 | 39.47–53.96 |
| Seven workers, drafting off | 34.75 | 33.50–35.84 |

Six workers did not produce a useful improvement. The selected profile retains
seven and DFlash2. It does not impose the earlier untested RAM/VRAM reserve values.
After restarting through this final launcher, six more performance runs gave a
45.20 tokens/s median, range 41.57–54.67, and 4K first-token latency 5.38–5.41 s.
The automatic CPU/PCIe split recalibrates at runtime. This repeat is not evidence
that making the same worker/drafter settings explicit creates a speedup.
This is not a claimed speedup from modified CUDA code. The automatic tuner had
no eligible exact arms for this setup; these were manual one-setting comparisons.

The 33.02 GiB expert arena stayed in pinned system RAM; its GPU cache was 1.44 GiB.
The PLE/ngram table used SSD reads. The engine process occupied about 35.6 GiB
physical RAM. During the measured Flash interval, whole-system GPU usage was
6.41–6.86 GiB, available RAM 4.74–5.54 GiB, and sampled peak GPU temperature 81 C.
Existing applications were left running. Windows commit headroom reached 0.48 GiB;
commit is not physical RAM use. The existing pagefile grew within its existing
limits; this setup did not change OS/pagefile settings. Plan other workloads using
both available RAM and commit headroom. Long unattended operation was not tested.

Use an installed Terminal. From this repository in PowerShell:

```powershell
.\local-assistant\start.ps1 -StrataCommand 'C:\path\to\bin\strata.cmd' -DataDirectory 'D:\LLM Models\Strata' -Plan
```

Omit `-Plan` to start. Add `-ModelPath` to reuse compatible GGUF files.
Stop another large GPU model before loading this profile. `-Tune` exposes the
upstream quick tuner, but should not be assumed to improve this measured profile.

## Comparison and quality limits

The accompanying `benchmark.py` uses three performance prompts twice (Russian,
code, and about 4K input tokens), 512 generated tokens each, plus eight quality
cases. All tested models used 32K context, greedy decoding and thinking off.
Exact requests, answers and timing records were saved during the local study.
The corpus SHA-256 is `ff1859309f8812fafccf1a84f08da1e435a794380699386751925b475a9b77d2`.

```powershell
python local-assistant/benchmark.py --backend strata --url http://127.0.0.1:8091 --model qwen3.8-flash-next-iq2_xs --out results/flash --suite all --repeat 2
```

The original Qwen3.6 Q4_K_M in LM Studio reached 42.15 tokens/s after downloads
finished. Its MTP UD-Q4_K_S Balanced profile reached 45.65 tokens/s. Earlier runs
during downloads were slower and are not the final baseline. These compare
complete model/runtime configurations, not an isolated ranking of engines.

The optional Qwen3.6 Writing profile (MTP1, all CPU expert layers, 4096 MiB GPU
expert cache / CPU share 0.5) reached 51.40 tokens/s, range 49.87–56.36. It had
slower first-token latency: 6.22–6.32 s at 4K input and 25.12 s at 16K. Its code
passed the same checks and its final Russian summary preserved the supplied facts.
However, on the separate synthetic tool fixture it skipped the function and
invented free disk space. It is a text-generation option, not a validated tool agent.

Flash's first token took 5.38–5.57 s with 4K input and 18.05 s with 16K input.
The completed Python repair took 8.60 s / 376 output tokens, versus 21.50 s / 855
tokens for the original Qwen3.6 and 13.06 s / 562 tokens for MTP Balanced.
Different answer lengths matter: tokens/s alone does not measure time to a useful answer.

After manual inspection, the Python answers passed 307 cases and the SQL SELECTs
passed six customer fixtures. All three configurations passed four of five strict
JSON tasks; the 16K answer contained correct facts but unwanted Markdown fences.
Flash invented an order number from an item quantity in the Russian summary.
Both Qwen3.6 summaries preserved those facts; their SQL prose incorrectly described
one redundant COALESCE case, although the repaired queries were correct.

Use Flash for reviewed drafts, code suggestions and bounded analysis. These few
tasks do not establish general superiority over Qwen3.6 or full-precision Flash.
Flash also passed one synthetic two-step tool-call check; no real tool was executed.

The final background worker completed a separate evidence-summary task in 7.78 s,
but still transferred verified LM Studio behavior to Strata despite the strengthened
system prompt. The corresponding Qwen3.6 Balanced answer preserved the association
(7.23 s). The task intentionally used a frozen earlier status, not the live download
state. Prompt changes did not fix Flash's factual association error. Qwen3.6 Balanced
remains the practical general-text recommendation; Flash is the reviewed code/draft
assistant being explored here. Neither is an unattended authority on facts.

## Bounded background work

Python 3.10 or newer, standard library only. With the server already running:

```powershell
python local-assistant/worker.py --state D:/assistant-jobs submit --input D:/tasks/task.txt
python local-assistant/worker.py --state D:/assistant-jobs work --url http://127.0.0.1:8091/v1 --model qwen3.8-flash-next-iq2_xs
python local-assistant/worker.py --state D:/assistant-jobs status
```

`work` drains pending jobs and exits. It can run in a background process.
On Windows use `Start-Process -WindowStyle Hidden` with redirected output.
No scheduled task or login service is installed.

One OS lock permits one worker per queue. SQLite records state; JSON results are
written atomically as `<job-id>.json`. A crashed running job becomes `interrupted`
on the next worker start and is not silently replayed. Submit a new job to retry.

Defaults: 2,048 output tokens, 180 seconds of wall time, no thinking, at most
24,000 UTF-8 input bytes. Submission options: `--max-tokens` (up to 4,096),
`--deadline` (up to 900 seconds), `--reasoning none|low`. The byte cap is not a
tokenizer measurement. Split larger inputs explicitly.

The endpoint must be localhost HTTP `/v1`. Redirects and HTTP proxies are disabled.
An optional key comes from `LOCAL_ASSISTANT_API_KEY`, never saved in job results.
Generated commands and tool calls are returned as data, never executed.
`completed` means the request finished; it is not factual validation. The system
prompt emphasizes preserving identifiers and associating measurements with the
correct model/runtime, but supervision is still necessary.

The timeout kills the request subprocess and closes its connection. Server-side
cancellation depends on the server. A token-limit hit or empty answer is `incomplete`.

## LM Studio fallback

LM Studio 0.4.28 build 2 honors `reasoning_effort: "none"` on
`/v1/chat/completions`; the worker supplies it. The native adapter also works:

```powershell
python local-assistant/worker.py --state D:/assistant-jobs work --url http://127.0.0.1:8123/v1 --model local-assistant-qwen36 --protocol lmstudio
```

The server/model must already be running. The native adapter uses `/api/v1/chat`,
does not store chats, and maps `none` to `off`, `low` to `on`. Its response lacks
`finish_reason`, so reaching the output cap is conservatively marked incomplete.
Its `on` mode has no enforced separate reasoning budget.

## Validation

```powershell
python -m unittest discover -s local-assistant -p test_worker.py -v
```

Four tests cover local generation/truncation, deadlines/crash recovery, exclusive
queue ownership/input limits, and native LM Studio reasoning control. The launcher
plan and real model loads were also checked. Benchmark summaries accompany this
profile under `measurements/`; raw records remain in the local research workspace.
No Basalt benchmark was run: its primary GPU requires Blackwell sm_120; the laptop
has a single Ada sm_89 card.
