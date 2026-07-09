---
name: video-production
description: Produce and deliver a finished video from an approved plan (or from clips someone hands you) using the clip-editor MCP — cutting, stitching, vertical framing, burned-in captions, royalty-free music, name/title header chyrons, and branded title/closing slates with the Emerge logo. Use when the story is already planned and it's time to render, or for a straightforward "edit these clips" / "cut this to 30 seconds" / "stitch these together" / "remove the interviewer" / "make it vertical" / "add captions" / "add music" / "put their name on screen" / "add a title card" / "add our logo" request. This is the PRODUCE step of the creative-director pipeline — it runs after the clips and narrative are chosen, not before.
---

# Video Production

Turn an approved plan — or a set of clips someone hands you — into a finished,
shareable video using the **clip-editor** connector. This is the last step of the
**creative-director** pipeline: exploration (student-highlights) and narrative
planning happen first, then this skill renders and delivers.

You can also use it directly for simple edits ("cut this to 30 seconds", "remove
the interviewer", "stitch these three together") without the full pipeline.

**Design goal: a non-technical teammate should be able to say "make it vertical
with captions and gentle music" and get a great result without answering a pile
of technical questions.** Use the defaults below; only ask when a choice really
changes the outcome.

## What you need before rendering

- The **Drive file IDs** for each source clip (.mp4) and its transcript (.txt).
- The **order of beats** — which segments from which clips, in what sequence.

If the narrative isn't decided yet, stop and go through **creative-director**
(brief → find clips via student-highlights → plan the story) first. Don't render
before the story is chosen.

## The clip-editor tools

- `clip-editor:list_clips(student_folder_id)` — list a student's clips + transcripts. Start here for file IDs.
- `clip-editor:parse_transcript(file_id)` — parse a transcript into indexed segments (index, start, end, speaker, text). **Always parse before editing** — you pass these segment indices to the edit tools.
- `clip-editor:preview_edit(file_id, keep_segments|keep_ranges)` — cheap dry run of one clip's duration/content. Call freely.
- `clip-editor:edit_clip(...)` — trim ONE clip (fast stream-copy, ~2s keyframe precision). For simple single-clip cuts.
- `clip-editor:stitch_clips(parts, output_name, aspect, frame_speaker, captions, music, music_bed, ...)` — the main tool: takes segments from one or more clips, re-encodes to a common format, concatenates in `parts` order. Frame-accurate. Use for anything multi-beat.

## Workflow

1. **Parse** each source transcript (`parse_transcript`) and confirm the segment indices for each beat.
2. **Preview** durations if unsure (`preview_edit`).
3. **Stitch** — one `stitch_clips` call. Output plays in `parts` order (`parts[0]` first); within a part, segments emit in the order listed in `keep_segments`.
4. **Fix the download URL** (see below) and **deliver** with a short arc summary.

```
clip-editor:stitch_clips(
    parts=[
        {"clip_file_id":"<mp4>", "transcript_file_id":"<txt>", "keep_segments":[4,5,6], "label":"struggle"},
        {"clip_file_id":"<mp4>", "transcript_file_id":"<txt>", "keep_segments":[9],     "label":"turning point"},
        {"clip_file_id":"<mp4>", "transcript_file_id":"<txt>", "keep_segments":[12,13], "label":"resolution"}
    ],
    output_name="Firstname - Story.mp4",
    aspect="9:16",
    frame_speaker="right",          # verify the side first — see Framing
    captions=true,
    music_bed="hopeful"             # or a two-act `music` block — see Scoring
)
```

Renders run ~3–5× realtime; captions and music each add a pass. Don't auto-render
more than ~3 minutes of output without a final go-ahead.

## Framing (aspect + which person)

**Aspect** (`aspect`, default `"16:9"`):
- `"9:16"` vertical (TikTok/Reels/Shorts) — the default for social. Aliases: `vertical`, `tiktok`, `reels`.
- `"1:1"` square (Instagram feed) · `"4:5"` portrait · `"16:9"` widescreen (internal review).

**Which person** (`frame_speaker`, default `"none"`): cal.com interviews are two
side-by-side panels (student + Emerge interviewer). `"right"`/`"left"` crop onto
that panel; `"none"` keeps the whole frame (letterboxed).

⚠️ **The student is NOT always on the same side** — it varies per recording.
Picking the wrong side silently frames the *interviewer* for the whole video.
**Verify first**: render/pull one frame with `frame_speaker="none"` and look (the
interviewer is the Emerge staffer — often headphones + office/bookshelf
background), then set the side. Each part can set its own `frame_speaker` (needed
for multi-student compilations where the side differs per interview). If you
can't verify, ship `"none"`.

## Captions

`captions=true` by default for anything meant for sharing (essential for silent
mobile viewing): lowercase white text, thick black outline, 3 words per line,
positioned in the lower third inside the social-app safe zone, auto-scaled to the
canvas. Set `false` only if the teammate explicitly wants a clean, text-free video.

## Scoring (music)

Two ways to score — pick by tone. All bundled tracks are **royalty-free** (Kevin
MacLeod, CC BY 4.0; see `clip-editor/assets/music/CREDITS.md`).

**A) `music_bed` — one continuous gentle track (default for sensitive stories).**
Best for a single-person testimony, a recovery/grief/veteran story, or anything
where a triumphant swell would feel exploitative. One track, start to finish,
gentle fade in/out.
```
music_bed="hopeful",        # bundled: hopeful | calm | cinematic | uplifting  (or a file path)
music_bed_volume=0.25,      # 0.18 quiet … 0.30 present
music_bed_start=0.0         # skip a soft intro (e.g. 10 for "hopeful")
```

**B) `music` — two-act rising→triumph score (classic struggle-to-win arc).**
Only when the cut has a real turning point (a spoken hinge line). Overkill/wrong
for a flat testimony.
```
music={ "rising_action_through_part": 1, "triumph_from_part": 3, "music_volume": 0.22 }
```
Parts up to `rising_action_through_part` get rising music; parts between play
voice-only (the turning-point line lands clean); parts from `triumph_from_part`
on get triumph music. If the turn is instant, set `triumph_from_part =
rising_action_through_part + 1`.

`music` and `music_bed` are **mutually exclusive** — pass one. For no music, omit both.

**Choosing:** sensitive/testimony → `music_bed` (`hopeful`/`calm`). Clear arc →
two-act `music`. Upbeat win → `uplifting`. "Music feels too dramatic / doesn't
fit" → switch to a `music_bed` and drop the volume.

**Attribution:** publishing with the bundled tracks needs a credit line —
*"Music by Kevin MacLeod (incompetech.com) — licensed under CC BY 4.0"* — in the
description. For zero-attribution, pass your own CC0 track path to `music_bed`.

## Delivering the result

**Fix the download URL before sharing** — the server returns `/health/downloads/`
in the path; strip `/health`:
- ❌ `…up.railway.app/health/downloads/{token}/{file}`
- ✅ `…up.railway.app/downloads/{token}/{file}`

Links expire in 24 hours. Share the link with a 3–5 line **arc summary** so the
teammate knows what they're about to watch (what you kept, where the turn falls),
and offer one round of easy changes (swap a beat, change the music, adjust length).

## Guardrails (catch these automatically — don't make the teammate think about them)

- **Repeated-clip trap.** A student's highlight and dimension clips are cut from
  the *same* interview and overlap in source time, so the same moment can appear
  in two clips — placing them adjacent replays footage. `stitch_clips` returns a
  `warnings` array flagging this; **if it's non-empty, re-pick before delivering.**
- **Dangling last segment.** The final transcript segment has no end time (the
  server estimates it) — avoid making it your closing beat, or the ending runs long/short.
- **Always parse first.** Segment indices come from `parse_transcript`, not the raw transcript's line numbers.
- **No-timestamp transcripts** can't be edited (older clips) — `parse_transcript`
  errors with "No timestamped segments found"; skip that clip and say so.
- **Don't chain `edit_clip` into `stitch_clips`** — stitch takes original Drive IDs, not download URLs.
- **500 MB source cap** per file (clear error before download).

## On-screen branding — name/title headers + title/closing slates

Native to `stitch_clips` — teammates get the flagship branded look self-serve.

**Name/title header chyron.** Add `header` (and optional `subheader`) to a part;
it shows a top-center name tag for the first ~3s of that part. Put it on the part
where the person first appears.
```
parts=[{ ..., "header": "Michael Dimick", "subheader": "U.S. Army Veteran · Lynn, MA" }]
```

**Title / closing slates.** `intro` and `outro` are branded cards (centered
title/subtitle + the Emerge logo, gentle fade) before/after the video:
```
intro={"title": "He served in the U.S. Army.", "subtitle": "After 9/11, he worked Ground Zero.", "seconds": 4},
outro={"title": "A fresh start, a new career,\nit all starts here.", "seconds": 4}
```
- `title` (required; use `\n` for a line break), `subtitle` (optional), `seconds`
  (default 4), `logo` (default true — shows the Emerge logo; set false to hide).
- Pair with a `music_bed` so the bed carries continuously under the slates — the
  recipe for a polished funder/testimony piece: intro states who they are, outro
  carries the tagline + logo.

**Still local-only (route to an engineer).** Two finishing touches aren't native
yet: tight single-panel crops for a specific box (needed to frame *gallery*
recordings — where both people sit in a middle band — without letterboxing), and
phrase-timed captions for very long single segments.

## What this skill is NOT for

- Finding clips or deciding the story — that's **student-highlights** (find) and
  **creative-director** (shape). Come here once the plan exists.
