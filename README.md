# RackWatch

> **Pre-existing vs. new:** Everything in this repository was created during the Meta Global AI Developer Hackathon, Nov 2–4, 2026. No pre-existing code is included. Public Meta docs and examples (meta-model-cookbook, legal-agent-workshop) were used as references and are credited in [THIRD_PARTY.md](THIRD_PARTY.md).

Snap a photo of a server rack and get a CMDB (configuration management database). Get hit with 512 alerts and get 3 verified incidents, each with a ticket and a client email waiting for a person to approve.

![The run view after verification: 512 alerts sorted into 3 incidents and a block of noise, with the verifier's two attempts above them](docs/screenshots/run-verified.png)

MSPs (managed service providers) run IT for dozens of small clients with a few techs on nights. One failed switch fires hundreds of alerts across everything behind it, and at 2 AM someone has to work out that it's one incident, find the cause, open a ticket and email the client without saying anything false. RackWatch does the legwork and shows its evidence. A person still signs off.

## Meta models used
| Model | Used for |
|---|---|
| `muse-spark-1.3` | Triage agent with tools, label reading, device grounding, effort routing, voice note work logs |
| `sam-3.1` | SAM (Segment Anything Model): segmenting each rack device, with concept and box prompts |
| `muse-voice-transcribe-1.0` | Tech voice notes, biased with keywords from the CMDB |
| Muse Code | Coding agent that wrote the vision pipeline and data generator ([session log](docs/muse-code-log.md)) |
| `muse-image-1.0` | Dev time only: made the sample rack image photorealistic |

Every runtime model call is Meta's, through one client, and the Call log screen shows all of them.

## Quick start
You need Python 3.11+ and Node 20+.

**No key: recorded runs**
```bash
python3 scripts/rw.py setup
python3 scripts/rw.py replay
```
Open http://127.0.0.1:8000 and click **Replay recorded run**. These are real recorded Meta calls. The tools and verifier run for real on your machine, and the screen says it's a replay. (`make setup` and `make replay` do the same.)

**Live**
```bash
cp .env.example .env
```
Add your `MODEL_API_KEY` to `.env`, then:
```bash
python3 scripts/rw.py dev
```
Live voice notes also need `ffmpeg`. A live triage costs about $0.30.

## How it works
See [docs/architecture.md](docs/architecture.md) for the diagram.

1. **Rack to CMDB.** SAM 3.1 and Muse Spark find each device. Spark reads the labels and rail numbers. Anything uncertain is flagged for review, and a person can correct any cell.
2. **Scrub.** Alerts and logs lose their PII (personally identifiable information) at ingest. Tokens like `<EMAIL_1>` map back only in a local table, and only after a person approves.
3. **Investigate.** The agent gets a digest, not 512 alerts, and works through tools: `cmdb_lookup`, `list_alerts`, `log_search`, `ping_sim`, `dns_sim`, `kb_search`, `draft_ticket`, `draft_client_email`, `submit_incidents`.
4. **Verify.** A plain Python verifier checks the submission. If it fails, the agent gets the reasons and re-plans, up to 3 attempts.
5. **Approve.** A person reads the evidence, adds a voice note if they're onsite, then approves or escalates. Only then can the ticket and email be exported.

## The verifier
The agent can't edit it, configure it, or call it except through `submit_incidents`. No tool can read ground truth.

| Rule | Checks |
|---|---|
| R1 citations exist | Every claim cites at least one log line |
| R2 citations real | Each cited line exists, and a `log_search` in this run returned it |
| R3 relevance | The line is from the root cause, an affected device or their uplink, within ±30 min of the alerts |
| R4 device resolves | The root-cause device is in the CMDB and the claims state its rack position in U (rack units) correctly |
| R5 coverage | Every alert is assigned exactly once |
| R6 grouping | Each incident matches a true incident (Jaccard ≥ 0.8) with the right root-cause device |
| R7 fix allowed | The fix is approved and addresses this cause, or the agent escalates |
| R8 drafts clean | Ticket cites 2+ logs. Email has no raw PII, log ids or jargon, in at most 180 words |
| R9 budget | Hard stop at the run cap, not a re-plan |

**An honest note on R6.** It compares against seeded ground truth, which is fair for a demo with seeded data and wouldn't exist on a real feed. Its feedback names conflicts, never answers: no truth ids, no expected device. With `production=True`, R6 and the truth half of R7 switch off and everything else still applies, which is a weaker check.

**The re-plan in the demo is real.** A rushed mode (`demo_strict`: low effort, 6 turns, first attempt only) often fails on its merits, so the rejection on screen actually happened. The shipped recording was kept because attempt 1 really failed (F1 0.59, 118 alerts unassigned, 8 drafts and positions wrong) and attempt 2 really passed. A normal run usually needs fewer attempts; see Results.

## PII
Deterministic regexes catch email, phone, MAC (media access control) addresses, card-like and SSN-like (Social Security number) numbers, IP (Internet Protocol) addresses outside lab ranges, `user <name>` and `Hi <Name>` patterns, plus the names in the PSA (professional services automation) contact list. `meta_client` scans every outgoing request and refuses the call if anything still matches. Client emails are de-tokenized locally, after approval. The token map never leaves the machine.

## Results
Measured on Nov 2, 2026 on the seeded dataset (seed 42), live against the Meta Model API. Raw numbers are in [docs/eval.json](docs/eval.json).

**Triage, 5 runs**
| | |
|---|---|
| Passed the verifier | 5 of 5 |
| Passed on the first attempt | 2 of 5 (mean 1.8 attempts) |
| Grouping F1 (the harmonic mean of pairwise precision and recall) | 0.996 mean (0.99 to 1.0) |
| Time | 58 s mean, 74 s worst |
| Estimated cost | $0.30 mean, $0.32 worst |

**Rack scan, 5 runs of the sample image**
VISION_RESULTS

Costs are estimates from published Model API prices and the usage each response reports.

**Limits.** The dataset is synthetic, with three incidents and a known answer, so these numbers show the system works on this scenario and don't predict a real NOC (network operations center). The sample rack is a generated image with clean stickers, and a real closet photo will be harder. Reading a model number partly hidden behind cables is unreliable, and the table flags low-confidence cells.

## Cost and budget guard
`budget.py` refuses a call before it's made if its estimate would pass $1.50 for the run or $120 in total. Totals persist in the call log. Every call sets `max_output_tokens`. Prompt caching does much of the work here: in the shipped recording, 54% of input tokens were cached. Tests use a fake client and cost nothing.

## What Muse Code built
Two agents built this, and the commit trailers say which did what. Muse Code (`Agent: muse-code muse-spark-1.3`) wrote `backend/vision/` and `datagen/` in AGENT_SESSIONS logged sessions. Run `scripts/agent_stats.sh` for commit and line counts.

The contract came first (`backend/app/schemas.py`), Muse Code coded to it, and live runs drove the rest: its second and third sessions were bug reports with measured numbers from the integrator. Where the build departed from the original spec, [docs/decisions.md](docs/decisions.md) says why.

## Tests
`pytest` covers every verifier rule with passing and failing fixtures, the PII scrubber (including false positives), tools, budget refusal, cassette round trips, scripted agent runs (pass, fail then re-plan, budget exhaustion), the API end to end in replay with no key, and the shipped recording itself. CI (continuous integration) runs them with no secrets, plus `ruff`, a secret scan and the frontend build.

## Data
Synthetic only, with no client PII. The rack image is generated, not a photo of real equipment. See [DATA.md](DATA.md).

## License
MIT for this code ([LICENSE](LICENSE)). Meta models and APIs are under Meta's terms. See [NOTICE.md](NOTICE.md), [SECURITY.md](SECURITY.md) and [THIRD_PARTY.md](THIRD_PARTY.md).

## Glossary
| Term | Meaning |
|---|---|
| API | Application programming interface |
| CI | Continuous integration |
| CMDB | Configuration management database |
| DNS | Domain Name System |
| F1 | The harmonic mean of precision and recall |
| IP | Internet Protocol |
| IT | Information technology |
| MAC | Media access control |
| MIT | The MIT open source license, named for the Massachusetts Institute of Technology |
| MSP | Managed service provider |
| NOC | Network operations center |
| PII | Personally identifiable information |
| PSA | Professional services automation |
| SAM | Segment Anything Model |
| SSN | Social Security number |
| U | Rack unit, the standard height of rack equipment |
