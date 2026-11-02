# Submission kit

Drafts for you to put in your own words, updated from DESIGN.md §20–22 with measured numbers. Every number here is in `docs/eval.json` or a cassette's `meta.json`.

## Before you submit
- [ ] Re-read the official rules and waiver. NOTICE.md still has a TODO on the exact wording, and DESIGN.md §17 lists what to check: the license grant, new-work-only rules, third-party and open-source disclosure, and whether the repo must stay public through judging.
- [ ] Decide on the sample rack image. It is a render made photorealistic with Muse Image, not a photograph, and DATA.md says so. A photo of real gear would land better on video, but its labels must match `data/rack/layout.json`.
- [ ] Replace `data/voice/sample_note.wav` with your own voice if you want a human on the recording. It is synthesized now.
- [ ] Confirm the X handles and hashtag.

## Video script, 3:00
Run it live or use Replay. Replay shows real recorded calls, and the screen labels it as a replay.

- **0:00–0:15, the hook.** Alert wall on screen. "2:07 AM. 512 alerts. One tech. Which one matters?"
- **0:15–0:45, the rack.** Rack tab, Scan. Boxes land on each device and the CMDB fills. Point at the accuracy line and at an amber cell: it flags what it isn't sure of.
- **0:45–1:30, triage.** Alerts tab, then the strict live run or Replay. Tools stream on the left, Meta calls on the right.
- **1:30–1:55, the rejection.** Attempt 1 lands and the lamps go red: alerts unassigned, U positions missing, drafts missing. The tiles sort wrongly, with 302 in noise. "It can't argue with this. It's plain Python." It re-plans. Attempt 2 goes green at 180, 60, 40 and 232 noise.
- **1:55–2:15, the voice note.** Open the switch incident. Click a citation to show the real log line. Record the note, and it lands in the ticket.
- **2:15–2:35, the human.** Approve, then open the plain-English client email and the export. Point out that nothing goes out without a person.
- **2:35–2:50, the numbers.** Metrics tab. "Every model call is Meta's, and here's the log."
- **2:50–3:00, the close.** "No key? Click Replay. github.com/<user>/rackwatch."

## Project description
RackWatch turns a photo of a server rack into a live asset inventory, then uses that inventory to triage alert storms. SAM 3.1 and Muse Spark find each device and read make, model, serial and rack position into a CMDB. When hundreds of alerts hit at 2 AM, a Muse Spark agent investigates with tools (CMDB, log search, simulated ping and DNS, a knowledge base) over PII-scrubbed data, and collapses the flood into a few incidents whose root causes cite exact log lines. A deterministic verifier the agent can't influence checks every claim, the grouping, and that the fix is approved, and it forces a re-plan if anything fails. A person approves the ticket and client email, and techs add voice notes transcribed by Muse Voice Transcribe.

## Use of Meta products
`muse-spark-1.3`: agent with tool calling, reasoning effort routing, encrypted reasoning across turns, JSON output, image grounding and label reading. `sam-3.1`: per-device segmentation with concept and box prompts. `muse-voice-transcribe-1.0`: voice notes with keyword biasing from the CMDB. Muse Code wrote the vision pipeline and synthetic data generator (two logged sessions). Muse Image made the sample rack image at dev time. Every runtime model is Meta's, and the call log is visible in the UI.

## Why Meta's models matter
One model family covers seeing (SAM 3.1 and Spark vision), reasoning with tools (Spark) and listening (Voice Transcribe), so one API key and one trust boundary handle the whole MSP workflow. Spark's effort dial spends high reasoning only on planning and re-plans. Prompt caching keeps long agent runs cheap. Open weights give MSPs a path to self-host for client-data control.

## X post
2:07 AM. 512 alerts. One tech.
RackWatch: photo of the rack → SAM 3.1 + Muse Spark build the inventory → a Muse Spark agent turns the alert storm into 3 root causes, each citing a log line, checked by a verifier it can't fool.
A person approves. Done in about a minute.
Built for @AIatMeta #MetaHackathon @cerebral_valley [video] [repo]
