# GenAI Development Log

This file records the main uses of generative AI during development, as required for the assignment.

| Tool and Model | Purpose | Prompt or Summary | Output Used | Verification / Changes |
|---|---|---|---|---|
| ChatGPT — GPT-5.6 Sol | Requirements and program design | Discussed the assignment requirements and a reusable CSV profiling architecture in which Python performs the calculations before any LLM step | Structure for the profiler, output folders, quality checks, type/role inference, visualization selection, and graceful LLM failure behavior | Compared the implementation against the assignment requirements and ran the same program on both datasets |
| ChatGPT — GPT-5.6 Sol | Python implementation and debugging | Requested help implementing reusable profiling functions and checking edge cases rather than writing dataset-specific analysis | Portions of `src/profiler.py`, including profiling, statistics, relationship analysis, plotting, report generation, and LLM prompt construction | Executed both datasets end-to-end and inspected the CSV profiles, JSON summaries, reports, and plots |
| ChatGPT — GPT-5.6 Sol | Evidence-based narrative | Supplied the Python-generated summary and instructed the model to use only those verified results and not invent statistics, meanings, units, causes, or causal claims | Narrative bullets stored in each output folder's `llm_response.txt` | Quantitative statements were checked against `analysis_summary.json` and the deterministic insight section |
| ChatGPT — GPT-5.6 Sol | Documentation review | Reviewed installation steps, run commands, limitations, dataset-source documentation, and the video requirements | README and supporting documentation | Confirmed commands against the implemented CLI and checked that required files and source information were present |

## Verification approach

Python calculates the quantitative evidence first. The LLM receives a structured summary only after those calculations are complete. The report keeps deterministic findings separate from the narrative response so quantitative claims can be checked against `analysis_summary.json`. If the LLM is unavailable, the deterministic profiling workflow still completes.
