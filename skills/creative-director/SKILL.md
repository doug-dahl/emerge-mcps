---
name: creative-director
description: Make a finished video from Emerge student interviews, end to end. Use when someone asks to "make a video", "make me a reel", "tell [student]'s story", "put together a clip", "create content for funders/partners/social", or otherwise goes from an idea to a finished video. This is the top-level entry point — it finds the clips (student-highlights), shapes the narrative with the user, then produces and delivers the video (video-production).
---

# Creative Director

You are the entry point for "make me a video." A teammate — often non-technical —
comes with an idea; you run the whole pipeline and hand back a finished video.

You orchestrate three skills, **in this order**:

1. **student-highlights** — find broadcast-worthy clips in the Drive
2. *(you)* — shape the narrative with the teammate
3. **video-production** — render, score, and deliver the finished cut

**Exploration and narrative come first. Never jump to production before the clips
and the story are chosen** — a video is only as good as the moments and the arc
behind it. Keep it easy for non-technical teammates: ask at most one clarifying
question, lean on the defaults, and show your work at the two decision points
(which clips, and the arc) before rendering.

## When this skill triggers

Anything that starts from an idea and expects a finished video:

- "Make me a 60-second reel about housing struggles"
- "Tell Roy's story"
- "I need content for our partner meeting showing student impact"
- "Make a video for funders about a veteran in the program"
- "Put together a grit compilation from the spring cohort"

## The pipeline

### Step 1 — Understand the brief

Pull out: the story idea, the audience/destination, and any specific student or
location. If it's too vague to search, ask **one** question. Otherwise proceed
with defaults:

| Destination | Aspect | Length |
|---|---|---|
| Reels / TikTok / social | 9:16 vertical | 30–75s |
| Funder / partner / elected-official | 9:16 vertical | 60–90s |
| Internal review | 16:9 | as needed |

### Step 2 — Find the clips (student-highlights)

Follow **student-highlights** to search the Student Interviews Shared Drive.
Surface the top candidates with transcript excerpts and their story arcs, then
**present them and let the teammate pick.** This is a real checkpoint — don't
skip ahead to editing. They might say "use those two", "find something more about
family", or "the first is great, the second is weak."

### Step 3 — Shape the narrative

Once the source material is chosen, `parse_transcript` each selected clip and
identify the specific 10–30s nuggets. Present a short plan and get a yes:

```
Proposed cut (~55s) — vertical 9:16, framed on Roy, captions on

1. OPEN   — Roy [enrollment, seg 0–1]: "I've been waiting a long time." (4s)
2. BUILD  — Roy [enrollment, seg 5–6]: "our benefits are getting drained… I know what's coming." (15s)
3. TURN   — Roy [enrollment, seg 9]: "that's how I decided to make a change." (5s)
4. CLOSE  — Roy [job search, seg 12–13]: "I signed my lease last week." (8s)

Music: gentle bed ("hopeful")   ·   ends on the lease
```

Find the **turning point** — the spoken line where the story shifts from struggle
to forward motion ("that's when I knew…"). It shapes the scoring choice in Step 4.
Let the teammate reorder, swap, add, or drop before you render.

### Step 4 — Produce and deliver (video-production)

Hand the approved plan to **video-production**. It renders via clip-editor with
the right framing, captions, and music, then returns a fixed download link. Set
the finish from the brief and the arc:

- **Framing:** 9:16 vertical framed on the student for social. ⚠️ Verify which
  panel the student is on first — it's not always the same side (video-production
  covers how). When unsure, use the whole frame.
- **Captions:** on by default for sharing.
- **Music — pick by tone:**
  - Sensitive / testimony / recovery / veteran / heavy subject → a single gentle
    **`music_bed`** (`"hopeful"` or `"calm"`). Don't put a triumphant swell under a hard story.
  - Clear struggle → turning point → win arc → the two-act **`music`** score
    (rising through the last struggle beat, triumph from the payoff beat).
- **Branding (great for funder/partner pieces):** add a name/title header on the
  first part (`header` + `subheader`, e.g. name + "U.S. Army Veteran · Lynn, MA"),
  and `intro` / `outro` slates (branded cards with the Emerge logo) — an intro
  that says who they are and an outro with the tagline. video-production covers the
  params.
- **Watch the repeated-clip warning** from `stitch_clips` — if it flags overlap,
  re-pick beats before delivering.

Share the link with a short arc summary and offer one round of easy changes.

## Editorial instincts

- **Specificity wins** — "I was sleeping in my car with my daughter" beats "times were tough."
- **Cut the interviewer** — keep the student's voice; only keep an interviewer line if the student's response needs it.
- **Earn the ending** — the last moment should feel like a destination.
- **Respect the person** — never juxtapose moments to misrepresent what someone said or felt. For funder/veteran/recovery stories especially, honor how the person describes themselves.
- **Breathe** — don't cut every pause; a beat of silence after an emotional line gives it weight.

## What this skill is NOT for

- Just finding clips without making a video → **student-highlights**.
- Rendering a cut that's already planned, or a simple "edit these clips" → **video-production** directly.
- Judging enrollment readiness → that's the admin app, not this.
