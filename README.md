# research-coverage

A candidate-discovery skill for open-source tooling research. It runs complete searches across capability axes, pages through results, then compresses thousands of hits into a fixed-size review set that a language model can actually read.

The point is bounded context, not completeness. The tool proves which searches ran and which candidates were looked at. It never claims the pool is exhaustive.

## The problem

Ask GitHub for `agent memory` and you get 47,899 repositories. Ask for `dependency analysis tool` and you get 1,038. Piping any of that into a model is impossible, and taking only the first page hides the fact that 99% of the pool was never seen.

Two failure modes follow:

- Truncate silently, and the report says "checked" when it only saw 8 of 47,899.
- Fetch everything, and the session dies on context.

This skill does both halves at once: page deeper so the pool is real, then screen mechanically so the review set stays constant.

## How it works

```
plan.json (axes + queries)
   |
   +-- search / pages        raw hits, per-page snapshots, coverage recorded
   +-- screen (screen-v1)    deterministic scoring, hard filters, dedup
   |      -> cards/<axis>.md          what the model reads (fixed size)
   |      -> cards/<axis>.adjacent.json   just below the cut, awareness only
   |      -> cards/<axis>.deferred.json   everything else, reason recorded
   +-- inspect               read READMEs of chosen candidates
   +-- upstream              direct dependencies + licenses via deps.dev
   +-- review                shortlist / exclude / defer with a reason
   +-- audit                 mechanical checks, report, exit code
```

Screening is arithmetic, not judgement: keyword hit rate, repeat hits across queries, recency, stars, license presence, topic overlap. Hard filters drop archived repos, forks, and entries with neither description nor topics. Near-duplicate descriptions (Jaccard >= 0.8) collapse into one slot, because forks and mirrors otherwise eat the review budget.

Only the screened set requires a review reason. Candidates below the cut are recorded with their score and never charged against the audit, since the model has not read them.

## Install

Requires Python 3.10+ and an authenticated `gh` CLI. No API keys. `upstream` calls the public deps.dev API.

```bash
git clone https://github.com/mapleleo-hub/research-coverage
```

As an agent skill, copy `skill/` into your skills directory (for example `~/.claude/skills/research-coverage`). As a plain CLI, run `skill/scripts/coverage.py` from anywhere.

## Use

```bash
python skill/scripts/coverage.py init --plan plan.json --out research/run
python skill/scripts/coverage.py run   --run research/run
python skill/scripts/coverage.py screen --run research/run
python skill/scripts/coverage.py upstream --run research/run --repo owner/repo
python skill/scripts/coverage.py audit --run research/run
```

Then point the model at `research/run/cards/*.md` only.

A plan is three or more capability axes, each with at least two complete queries:

```json
{
  "topic": "reusable memory components for personal agents",
  "max_queries": 12,
  "max_inspections": 6,
  "limit": 30,
  "pages": 2,
  "screen": 25,
  "adjacent": 8,
  "max_upstream": 40,
  "axes": [
    {"id": "context", "label": "Memory and context", "queries": ["agent memory", "context database"]}
  ]
}
```

| field | meaning | range |
|---|---|---|
| `limit` | results per page | 1-50 |
| `pages` | pages per query, `limit * pages <= 1000` | 1-10 |
| `screen` | candidates shown per axis | 1-200 |
| `adjacent` | candidates listed just below the cut | 0-50 |
| `max_inspections` | README reads plus upstream calls | 2-20 |
| `max_upstream` | packages resolved per upstream call | 1-200 |

## What the numbers look like

Measured on one 3-axis run: 6 queries, 349 hits fetched, 340 unique candidates, 30 screened.

| cards | characters | share of candidate text |
|---|---:|---:|
| without adjacent | 7,461 | 7.5% |
| with adjacent 8 | 10,134 | 10.2% |

A separate sample of 30 candidates just below the cut found 12 that should have been reviewed (40%). `adjacent` recovered 10 of them for a 36% increase in card size. Raising `screen` to 20 recovered the same 10 while doubling review work, so the parameter is not the fix: coverage of the deep tail is set by score position, not by budget.

## Design decisions worth knowing

- **Coverage is not recall.** A query with 47,899 hits and 60 fetched records `coverage=0.0013`. That number describes what was fetched, never what exists.
- **Imports cannot substitute for native queries.** Provided result files count against budget but never satisfy per-axis native coverage.
- **Mechanical pass is not a verdict.** `process_checked` means the recorded procedure completed. It says nothing about whether the right candidates were found.
- **Screening kills long tails.** Keyword scoring pushes projects whose wording differs from the query to the bottom. Sampling says roughly 40% of the near-cut band is wrongly excluded, and the far tail is worse.

## Limitations

- Scoring is keyword arithmetic. It has no semantic understanding.
- Multi-word queries in GitHub are AND, so precise queries return very few hits while broad ones return noise.
- Ambiguous words (`license`, `graph`, `memory`) pull in whole unrelated domains. Always qualify them.
- `upstream` parses manifests with regex, does not resolve workspace or optional dependencies, and reports default versions rather than locked ones. deps.dev exposes no reverse-dependency endpoint.
- Changing queries between runs replaces the pool almost entirely (one overlapping candidate in 340 vs 189 across two wordings).

## Status

Design and validation notes are in [DESIGN.md](DESIGN.md) and [VALIDATION.md](VALIDATION.md). The contract review reached PASS from one model family and open disagreements from another; that is recorded rather than smoothed over.

## License

MIT
