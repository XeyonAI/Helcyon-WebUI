# Helcyon-Bench

Helcyon-Bench is a small Windows-friendly Streamlit app for interactively comparing two companion models across five visible prompts.

It is not an automated benchmark. The workflow is:

1. Optionally label Response A and Response B with model names.
2. Select a prompt pack from `prompt_packs` and load it, or type your own five prompts.
3. Paste Response A and Response B for each prompt.
4. Optionally save the visible prompts back to the selected prompt pack.
5. Click Judge.
6. Read the per-prompt results and averaged category scores.

## Setup

On Windows, double-click `Setup.bat`. It creates `.venv`, installs the dependencies, and creates `config.yaml` from `config.example.yaml` if needed.

For manual setup, install the minimal dependencies:

```powershell
pip install -r requirements.txt
```

Copy the example config:

```powershell
copy config.example.yaml config.yaml
```

Edit `config.yaml` with your OpenAI-compatible judge endpoints:

```yaml
judge:
  default_endpoint: "Local"
  endpoints:
    Local:
      base_url: "http://127.0.0.1:5000/v1"
      api_key: "local-key"
      model: "local-model"
      local_endpoint_mode: "shared_hwui"
      models:
        - "local-model"
      temperature: 0.0
      max_tokens: 1200
      timeout: 120

    Local Dedicated:
      base_url: "http://127.0.0.1:5001/v1"
      api_key: "local-key"
      model: "local-model"
      local_endpoint_mode: "external_dedicated"
      models:
        - "local-model"
      temperature: 0.0
      max_tokens: 1200
      timeout: 120

    OpenAI:
      base_url: "https://api.openai.com/v1"
      api_key: "sk-your-key-here"
      model: "gpt-4o"
      models:
        - "gpt-4o"
        - "gpt-5.5"
      temperature: 1.0
      max_completion_tokens: 4800
```

OpenAI, OpenRouter, llama.cpp, and similar compatible `/chat/completions` endpoints are supported.
`timeout` is the number of seconds Helcyon-Bench waits for a judge `/chat/completions` request before cancelling it; it defaults to 120 if omitted. For local endpoints, this is the ceiling on how long the model can generate before the request is cut off, independent of `max_tokens` — a slow local judge that would otherwise exceed 120s needs `timeout` raised (or `max_tokens` lowered) to avoid being cancelled mid-generation.
For the normal local workflow, choose `Local` to use HWUI's shared llama.cpp server on `5000` and the currently loaded HWUI model. Helcyon-Bench marks shared requests with `X-HelcyonBench-Run: true`; HWUI should preserve the llama-server while such a request or configured bench lock is active. `Local Dedicated` on `5001` is optional for machines with enough VRAM or a separate judge host.
When the local OpenAI-compatible judge server is running, click `Refresh Judge Models` to populate the Judge dropdown from `/v1/models`.
Use `Test Judge Connection` before a benchmark run to confirm Helcyon-Bench can reach `/v1/models` and complete a tiny structured `/v1/chat/completions` request with the selected judge model.
Before benchmark judging starts, Helcyon-Bench checks `/v1/models` and runs a tiny structured `/v1/chat/completions` smoke test.
Judge requests use structured output controls where available: JSON schema first, JSON object mode next, and llama.cpp grammar as the local fallback.

## Run

Double-click `Run.bat`, or run:

```powershell
streamlit run app.py
```

## Evaluation protocol

Every comparison is judged twice, by two independent judge calls:

1. Response A = Model A, Response B = Model B
2. Response A = Model B, Response B = Model A

The response text is never regenerated between the passes; only the slot assignment changes. Each pass's scores are mapped back to model identity, each model's criterion scores are averaged across its A-slot and B-slot appearances, and the canonical overall score is recalculated from those averages. The winner is decided from the canonical scores, not from either individual pass.

This exists because a judge that mildly prefers whichever response is shown first will otherwise hand a win to whichever model happens to occupy that slot. Both directional results are kept in the saved JSON and the report, and:

- If the winner changes when the order is reversed, the comparison is marked **position-sensitive** and is not presented as a clean win.
- The slot effect (Response A mean minus Response B mean) is reported per comparison and per run.
- The judge never sees candidate model names; it sees only Response A and Response B.

Because judging is bidirectional, a run makes two judge calls per prompt, and progress is reported in judge calls.

### Criterion applicability

A criterion that the prompt neither invites nor rewards (typically Humour on a prompt where levity would be unwelcome) is marked N/A for the comparison. N/A applies to both responses, is stored as null, and is excluded from both overall scores — it is never converted to 0, 8, or any other number. The remaining criteria are reweighted among themselves.

### Self-judging

Using one of the candidate models as the judge is still allowed, but it is disclosed before the run, in the results, in the report, and in the saved JSON. Bidirectional judging removes the slot effect; it cannot remove a model's preference for its own style.

### Judge output contract

Every judge response is normalised to one canonical shape before validation:

```json
{
  "criteria_applicability": {"<category>": "applicable | not_applicable"},
  "responses": {"A": {"scores": {}, "strengths": [], "deductions": {}, "weaknesses": []}, "B": {}},
  "comparison": {},
  "final_verdict": ""
}
```

The application, never the judge, calculates `overall`, `winner`, `confidence` and `score_difference`; any the judge supplies are discarded. Local judges are only loosely constrained when a run falls back to `json_object` mode, so a payload that puts the marks under `overall.A`/`overall.B` (or another container) is mapped onto the canonical shape instead of failing the run, with the repair recorded in a `normalisation` block. Local endpoints request `json_schema` first, then the llama.cpp GBNF grammar, then `json_object`.

### Older benchmark files

Benchmark runs saved before bidirectional judging still load and render. They are labelled as legacy single-pass records and are never treated as position-stable, because they were judged in one order only.

## Notes

- The app expects judge output as structured JSON.
- Judge debug logs are written to `judge_logs/*.jsonl` by default. Set `judge.debug_logging: false` in `config.yaml` to disable them.
- Reports can be exported as Markdown.
- The sidebar keeps a lightweight in-session history of recent winners.
- The app does not load or switch models. Generate responses elsewhere, then paste them in for comparison.
- Prompt packs are JSON files in `prompt_packs` and only require `title` and `prompt` entries.
- Packs may set an optional `"rubric"` field (e.g. `"companion"`) to pick the scoring rubric explicitly. When absent, Creativity packs use `creativity.md`, Philosophy packs `philosophy.md`, Morals packs `morals.md`, Uncensored packs `uncensored.md`, Humour packs `humour.md`, and everything else (including Companion) uses `companion.md`. Empathy/distress-signalling packs also get `distress_calibration.md` appended after the primary rubric.

