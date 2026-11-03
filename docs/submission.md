# Submission kit

Drafts for you to put in your own words, updated from DESIGN.md §20–22 with measured numbers. Every number here is in `docs/eval.json` or a cassette's `meta.json`.

## What the organizers require
From the [participant guide](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/details), `#rules` and `#faqs`, read Nov 2:

- **Open the video with one line saying what was pre-existing and what you built this week.** This comes from the FAQ. If judges can't tell what's new, the project is disqualified.
- The video is 3 minutes and shows only what was built during the event.
- The repo is public and contains only event work. Everyone who worked on it is listed on the submission page.
- Submit at [the event submission page](https://cerebralvalley.ai/e/meta-ai-developer-hackathon/hackathon/submit) by Wed Nov 4, 9:00 PM ET. Late entries don't count. Nov 3 is the last full day for submission questions.
- Judges score the video, the code **and the live product** ("Does the product hold up live?"). A hosted replay link would help here.
- **Most Viral ($20,000):** post the demo on X and submit that post's link on the platform. Engagement counts through the end of Nov 8.
- **Best Build on Meta Open Weight Models ($10,000):** requires Muse Glimmer or Muse Spark Open on your own hardware or partner inference, and the infrastructure partner verifies it. RackWatch runs its whole triage agent on Muse Spark Open through Baseten: 5 of 5 runs passed the verifier (`docs/eval.json`), and **Replay on open weights** shows a recorded run. We don't yet know how Baseten verifies usage.
- **Best Agentic Use-Case ($5,000):** judged on autonomy, effectiveness and practical usefulness. Other bonuses: Community Vote $10,000, Best Creative Build $5,000.

## Before you submit
- [ ] Re-read the waiver you accepted. The guide and `#rules` cover new-work-only and the public repo, but neither shows the license grant wording, so NOTICE.md keeps its TODO.
- [ ] Decide on the sample rack image. It is a render made photorealistic with Muse Image, not a photograph, and DATA.md says so. A photo of real gear would land better on video, but its labels must match `data/rack/layout.json`.
- [ ] Replace `data/voice/sample_note.wav` with your own voice if you want a human on the recording. It is synthesized now.
- [ ] Confirm the X handles and hashtag.
- [ ] Ask in `#baseten` how they verify usage for the Open Weight bonus, and whether a key from a personal Baseten account counts. The pinned post covers only the $100 hackathon key sent to selected builders.
- [ ] Pick the replay for the video. **Replay recorded run** has the bigger failure, because its first attempt was deliberately rushed. **Replay on open weights** failed on its own, with no rushing, and supports the bonus entry.

## Video script, 3:00
Run it live or use Replay. Replay shows real recorded calls, and the screen labels it as a replay.

- **0:00–0:05, required disclosure.** "Everything in this demo was built during the hackathon. Nothing was pre-existing."
- **0:05–0:15, the hook.** Alert wall on screen. "2:07 AM. 512 alerts. One tech. Which one matters?"
- **0:15–0:45, the rack.** Rack tab, Scan. Boxes land on each device and the CMDB (configuration management database) fills. Point at the accuracy line and at an amber cell: it flags what it isn't sure of, and the usual miss, a 2U server read as 1U, is flagged.
- **0:45–1:30, triage.** Alerts tab, then the strict live run or Replay. Tools stream on the left, Meta calls on the right.
- **1:30–1:55, the rejection.** Attempt 1 lands and the lamps go red: alerts unassigned, U positions missing, drafts missing. The tiles sort wrongly, with 302 in noise. "It can't argue with this. It's plain Python." It re-plans. Attempt 2 goes green at 180, 60, 40 and 232 noise.
- **1:55–2:15, the voice note.** Open the switch incident. Click a citation to show the real log line. Record the note, and it lands in the ticket.
- **2:15–2:35, the human.** Approve, then open the plain-English client email and the export. Point out that nothing goes out without a person.
- **2:35–2:50, the numbers.** Metrics tab. "Every model call is Meta's, and here's the log." For the bonus, add: "On Meta's open weights it passes the same verifier, at $0.18 a run instead of $0.30."
- **2:50–3:00, the close.** "No key? Click Replay. github.com/<user>/rackwatch."

## Project description
RackWatch turns a photo of a server rack into a live asset inventory, then uses that inventory to triage alert storms. SAM (Segment Anything Model) 3.1 and Muse Spark find each device and read make, model, serial and rack position into a CMDB (configuration management database). When hundreds of alerts hit at 2 AM, a Muse Spark agent investigates with tools (CMDB, log search, simulated ping and DNS, a knowledge base) over data scrubbed of PII (personally identifiable information), and collapses the flood into a few incidents whose root causes cite exact log lines. A deterministic verifier the agent can't influence checks every claim, the grouping, and that the fix is approved, and it forces a re-plan if anything fails. A person approves the ticket and client email, and techs add voice notes transcribed by Muse Voice Transcribe.

## Use of Meta products
`muse-spark-1.3`: agent with tool calling, reasoning effort routing, encrypted reasoning across turns, JSON output, image grounding and label reading. Muse Spark Open, through Baseten: the same agent on Meta's open weights. `sam-3.1`: per-device segmentation with concept and box prompts. `muse-voice-transcribe-1.0`: voice notes with keyword biasing from the CMDB. Muse Code wrote the vision pipeline and synthetic data generator (two logged sessions). Muse Image made the sample rack image at dev time. Every runtime model is Meta's, and the call log is visible in the UI.

## Why Meta's models matter
One model family covers seeing (SAM 3.1 and Spark vision), reasoning with tools (Spark) and listening (Voice Transcribe), so one API (application programming interface) key and one trust boundary handle the whole MSP (managed service provider) workflow. Spark's effort dial spends high reasoning only on planning and re-plans. Prompt caching keeps long agent runs cheap. Open weights give MSPs a path to control where client data goes: the same agent passes the same verifier on Muse Spark Open through Baseten, in 5 of 5 runs at $0.18 a run against $0.30 on the hosted model.

## X post
2:07 AM. 512 alerts. One tech.
RackWatch: photo of the rack → SAM 3.1 + Muse Spark build the inventory → a Muse Spark agent turns the alert storm into 3 root causes, each citing a log line, checked by a verifier it can't fool.
A person approves. Done in about a minute.
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
