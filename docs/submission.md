# Submission kit

Drafts for you to put in your own words, updated from DESIGN.md §20–22 with measured numbers. Every number here is in `docs/eval.json` or a cassette's `meta.json`.

## What the organizers require
From the [participant guide](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details), `#rules` and `#faqs`, read Nov 2:

- **Open the video with one line saying what was pre-existing and what you built this week.** This comes from the FAQ. If judges can't tell what's new, the project is disqualified.
- The video is 3 minutes and shows only what was built during the event.
- The repo is public and contains only event work. Everyone who worked on it is listed on the submission page.
- Submit at [the event submission page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit) by Wed Nov 4, 9:00 PM ET. Late entries don't count. Nov 3 is the last full day for submission questions.
- Judging, from the official rules: Impact 30%, Use of Meta AI Tooling 25%, Demo 25%, Technical Depth & Execution 20%. Demo covers the video and whether the product holds up live. The form has no field for a demo link, so the description below carries the hosted replay demo: https://ca-rackwatch-demo.mangograss-1e4cf20f.eastus2.azurecontainerapps.io
- **Most Viral ($20,000):** post the demo on X and submit that post's link on the platform. Engagement counts through the end of Nov 8.
- **Best Build on Meta Open Weight Models ($10,000):** requires Muse Glimmer or Muse Spark Open on your own hardware or partner inference, and the infrastructure partner verifies it. RackWatch runs its whole triage agent on Muse Spark Open through Baseten: 5 of 5 runs passed the verifier (`docs/eval.json`), and **Replay on open weights** shows a recorded run. We asked `#baseten` how they verify usage and had no answer by Nov 4.
- **Best Agentic Use-Case ($5,000):** judged on autonomy, effectiveness and practical usefulness. Other bonuses: Community Vote $10,000, Best Creative Build $5,000.

## Before you submit
- [x] Waiver read on Nov 4, from [the event page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/waiver). NOTICE.md now states its license grant.
- [x] Sample rack image: keep the render. The README says it's generated, and its labels match `data/rack/layout.json`.
- [x] Voice note: keep the synthesized sample. THIRD_PARTY.md says how it was made.
- [x] X tags: the event details ask only for the post link on the form, so no tag is required. The draft below tags Meta and Cerebral Valley.
- [x] Baseten: asked in `#baseten` on Nov 2 how they verify usage. No answer by Nov 4, so the entry goes in on the results below.
- [x] Replay for the video: **Replay on open weights**. It failed on its own, with no rushing, and it supports the bonus entry.
- [ ] Record the video, with one live segment.
- [ ] Submit the form and post on X.

## Video script, 3:00
Run the rack scan live with your key (`python scripts/rw.py dev`), with the Call log on screen, and say it's live. A scan takes about two minutes, so speed up the wait and label it. Use **Replay on open weights** for triage: those are real recorded calls, and the screen labels it as a replay.

- **0:00–0:05, required disclosure.** "Everything in this demo was built during the hackathon. Nothing was pre-existing."
- **0:05–0:15, the hook.** Alert wall on screen. "2:07 AM. 512 alerts. One tech. Which one matters?"
- **0:15–0:45, the rack.** Rack tab, Scan. Boxes land on each device and the CMDB (configuration management database) fills. Point at the accuracy line and at an amber cell: it flags what it isn't sure of, and the usual miss, a 2U server read as 1U, is flagged.
- **0:45–1:30, triage.** Alerts tab, then **Replay on open weights**. Tools stream on the left, Meta calls on the right.
- **1:30–1:55, the rejection.** Attempt 1 lands and the lamps go red: three incidents with no rack position, three alerts counted twice, and two different failures merged into one incident. "It can't argue with this. It's plain Python." It re-plans. Attempt 2 goes green at 180, 60, 40 and 232 noise.
- **1:55–2:15, the voice note.** Open the switch incident. Click a citation to show the real log line. Record the note, and it lands in the ticket.
- **2:15–2:35, the human.** Approve, then open the plain-English client email and the export. Point out that nothing goes out without a person.
- **2:35–2:50, the numbers.** Metrics tab. "Every model call is Meta's, and here's the log." For the bonus, add: "On Meta's open weights it passes the same verifier, at $0.18 a run instead of $0.30."
- **2:50–3:00, the close.** "No key? Click Replay. github.com/Acidni-LLC/rackwatch."

## Project description
RackWatch turns a photo of a server rack into a live asset inventory, then uses that inventory to triage alert storms. SAM (Segment Anything Model) 3.1 and Muse Spark find each device and read make, model, serial and rack position into a CMDB (configuration management database). When hundreds of alerts hit at 2 AM, a Muse Spark agent investigates with tools (CMDB, log search, simulated ping and DNS, a knowledge base) over data scrubbed of PII (personally identifiable information), and collapses the flood into a few incidents whose root causes cite exact log lines. A deterministic verifier the agent can't influence checks every claim, the grouping, and that the fix is approved, and it forces a re-plan if anything fails. A person approves the ticket and client email, and techs add voice notes transcribed by Muse Voice Transcribe. Try the recorded runs with no key at https://ca-rackwatch-demo.mangograss-1e4cf20f.eastus2.azurecontainerapps.io

## Use of Meta products
`muse-spark-1.3`: agent with tool calling, reasoning effort routing, encrypted reasoning across turns, JSON output, image grounding and label reading. Muse Spark Open, through Baseten: the same agent on Meta's open weights. `sam-3.1`: per-device segmentation. Concept prompts alone missed this rack, so Muse Spark grounding seeds SAM's box prompts. `muse-voice-transcribe-1.0`: voice notes with keyword biasing from the CMDB. Muse Code wrote the vision pipeline and synthetic data generator (five logged sessions). Muse Image made the sample rack image at dev time. Every runtime model is Meta's, and the call log is visible in the UI.

## Why Meta's models matter
One model family covers seeing (SAM 3.1 and Spark vision), reasoning with tools (Spark) and listening (Voice Transcribe), so one API (application programming interface) key and one trust boundary handle the whole MSP (managed service provider) workflow. Spark's effort dial spends high reasoning only on planning and re-plans. Prompt caching keeps long agent runs cheap. Open weights give MSPs a path to control where client data goes: the same agent passes the same verifier on Muse Spark Open through Baseten, in 5 of 5 runs at $0.18 a run against $0.30 on the hosted model.

## X post
2:07 AM. 512 alerts. One tech.
RackWatch: photo of the rack → SAM 3.1 + Muse Spark build the inventory → a Muse Spark agent turns the alert storm into 3 root causes, each citing a log line, checked by a verifier it can't fool.
A person approves. About three minutes, start to finish.
Built for @AIatMeta #MetaHackathon @cerebral_valley [video] [repo]

## Glossary
| Term | Meaning |
|---|---|
| API | Application programming interface |
| CMDB | Configuration management database |
| DNS | Domain Name System |
| JSON | JavaScript Object Notation |
| MSP | Managed service provider |
| PII | Personally identifiable information |
| SAM | Segment Anything Model |
| U | Rack unit |
