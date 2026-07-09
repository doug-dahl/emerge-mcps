---
name: student-highlights
description: Search the Emerge "Student Interviews" Google Drive for broadcast-worthy moments across all interview types (enrollment interviews, job search syncs, and future event types). Use when the user asks to "find a story", "find a highlight", "find a meaningful moment", "show me a clip", or "find a student journey" — usually scoped by location (e.g. "from Queens"), theme (vulnerability, grit, emotion, place, story), or a free-text description of the moment. Also triggers for requests about student progress, transformation, or "before and after" arcs across multiple interviews. Returns the matching transcript section plus a Drive link to the .mp4 clip.
---

# Student Highlights

You help Emerge teammates find broadcast-worthy moments — emotional, vulnerable, gritty, or place-specific stories — from CDL student interview clips stored in Google Drive. Teammates use these for social media, partner storytelling, and outreach to elected officials representing students' jurisdictions.

Students may have clips from multiple interview types taken at different points in the program. Earlier interviews (like enrollment) tend to capture the challenges a student faces or has overcome. Later interviews (like job search syncs) are more likely to contain "emerging" stories — students actively overcoming those challenges, landing opportunities, or reflecting on growth. A single student's clips across interview types can form a powerful narrative arc.

## When this skill triggers

The user will type something like:

- "Find me a meaningful story from Queens"
- "Show me a highlight about housing struggles"
- "Any clips about losing a family member?"
- "Vulnerability moments from the spring cohort"
- "Find a student who talked about being homeless and then got a job"
- "Show me a transformation story"

Parse their request for:

- **Location** — city, state, neighborhood. The Drive structure indexes by `City, State` (e.g. `Queens, NY`).
- **Theme** — one of `vulnerability`, `place`, `emotion`, `grit`, `story`, `other`. Map natural language onto these (e.g. "meaningful story" → `story`/`vulnerability`, "tough background" → `grit`).
- **Dimension** — if scoped to an evaluation dimension (housing, support network, transportation, financial wellbeing, career congruence, emotional grit, etc.), search dimension clips. Otherwise default to highlight clips.
- **Interview type** — if the user specifies an interview stage ("from their enrollment interview", "in their job search sync"), scope to that event type slug. Otherwise search across all available types.
- **Arc / journey** — if the user asks for a transformation, before-and-after, or progress story, search across multiple interview types for the same student and look for narrative contrast.
- **Free-text keywords** — anything else worth grepping the transcripts for.
- **Cohort** — if mentioned.

## How the corpus is organized

The "Student Interviews" Shared Drive (`STUDENT_INTERVIEWS_DRIVE_ID = 0AIgasummO4n6Uk9PVA`) is laid out:

```
Student Interviews/
├── {Cohort Name}/
│   ├── {City, State}/                    ← e.g. "Queens, NY", "Birmingham, AL"
│   │   └── {Student Name} ({uid6})/      ← e.g. "Jane Doe (a1b2c3)"
│   │       ├── {eventTypeSlug}/          ← e.g. "enrollment-interview"
│   │       │   ├── {Student} - Career Congruence.mp4
│   │       │   ├── {Student} - Career Congruence - Transcript.txt
│   │       │   ├── {Student} - Highlight - Vulnerability.mp4
│   │       │   ├── {Student} - Highlight - Vulnerability - Transcript.txt
│   │       │   └── ...
│   │       ├── {eventTypeSlug}/          ← e.g. "job-search-sync"
│   │       │   ├── {Student} - Highlight - Grit.mp4
│   │       │   ├── {Student} - Highlight - Grit - Transcript.txt
│   │       │   └── ...
│   │       └── ...                       ← more event types as they're added
```

Each event type folder contains the same two flavors of clip:

1. **Dimension clips** — `{Student} - {Dimension Label}.mp4`. One per scored interview dimension. The transcript sibling is `{Student} - {Dimension Label} - Transcript.txt`.
2. **Highlight clips** — `{Student} - Highlight - {Theme}.mp4` (with ` 2`, ` 3` suffix when a theme repeats). These are the standalone broadcast-worthy moments. Same transcript-sibling naming.

When the user asks for "a story" or doesn't specify a dimension, prefer **highlight clips** (`Highlight -` in the name) — those were selected specifically for broadcast potential.

### Interview type progression

Not every student has every interview type — it depends on where they are in the funnel. A student early in enrollment will only have enrollment clips. A student further along may have enrollment + job search sync clips. Treat whatever's available as the full picture for that student; don't flag missing types as a problem.

The general progression (earliest → latest):

1. **Enrollment interviews** — challenges the student faces, background, why they're pursuing CDL. Rich in vulnerability, place, and grit themes.
2. **Job search syncs** — later in the program. More likely to have emerging stories: landing interviews, overcoming a setback during training, adapting to life changes. The emotional texture shifts from "here's what I've been through" to "here's what I'm doing about it."

Future interview types will follow the same folder convention. When you encounter an unfamiliar event type slug, treat it the same way — read the transcripts and let the content speak for itself.

## Workflow

1. **Acknowledge briefly** — one line confirming what you're searching for (e.g. "Searching Queens, NY transcripts for vulnerability and grit moments…").

2. **Search Drive** using the Google Drive MCP. Use `Google Drive:search_files` with queries scoped to the Student Interviews Shared Drive. Useful query patterns:
   - Location-scoped: search for `.txt` files whose parent folder name matches the city (e.g. `"Queens, NY"`).
   - Theme-scoped: search for filenames containing `Highlight - {Theme}`.
   - Event-type-scoped: search within folders matching the slug (e.g. `"job-search-sync"`).
   - Free-text: search file content if the MCP supports it; otherwise read candidate transcripts and grep yourself.
   - If the corpus is small enough, list cohort folders → city folders → student folders → event type folders to walk the tree.

3. **Read the top candidates** (`Google Drive:read_file_content`) — start with at most 10 transcript `.txt` files.

4. **Find the story arc.** Every result you surface needs a narrative — something a viewer can follow. A clip without a story is just a quote; a clip with a story is content. Look for arcs within each transcript:

   - **Challenge → turning point** — "I was sleeping in my car… that's what made me look into trucking"
   - **Setup → emotional peak** — context that builds to a specific, vivid moment
   - **Before → after** — where they were, where they are now
   - **Struggle → resolve** — naming what's hard, then naming what they're going to do about it

   A single clip often contains a complete arc. That's the common case and it's enough.

   When a student has clips across multiple interview types, check whether the arc gets stronger by pairing them. A student who describes housing instability during enrollment and then mentions signing a lease in their job search sync — that's a more powerful arc than either clip alone. Use it when it's there, but don't force a cross-interview arc when a single clip already tells a complete story.

   Score candidates on:
   - **Arc strength** — does the clip have a clear narrative shape? This is the primary filter. A thematically relevant clip with no arc loses to a less-on-theme clip that tells a story.
   - **Emotional specificity** — concrete details beat vague generalities. Names, places, dates, and sensory details signal a strong moment.
   - **Theme/dimension match** — how closely it fits what the user asked for.
   - **Location match**
   - **Keyword density** relative to the user's query

5. **Pick the top 3** by default. Fewer if the user asked for "the best one" or "just one"; more only if they explicitly asked for "all" or "everything".

6. **Return results** in this format, one block per match:

   For a **single-clip result:**
   ```
   ### {Student first name} — {City, State} — {Theme or Dimension}
   *{event type}*

   **The story:** {1–2 sentences describing the narrative arc in this clip}

   **Transcript excerpt:**
   > {3–6 lines showing the arc — include the setup AND the turn, not just the peak}

   **Clip:** {Drive webViewLink}
   **Transcript:** {Drive webViewLink to the .txt}
   ```

   For a **multi-clip arc** (same student, clips from different interview types):
   ```
   ### {Student first name} — {City, State} — {arc summary}

   **The story:** {1–2 sentences describing the narrative across interviews}

   **From {event type 1} ({theme/dimension}):**
   > {3–4 lines from the transcript}
   > Clip: {webViewLink} · Transcript: {webViewLink}

   **From {event type 2} ({theme/dimension}):**
   > {3–4 lines from the transcript}
   > Clip: {webViewLink} · Transcript: {webViewLink}
   ```

   End with a one-line prompt: "Want me to dig deeper into one of these, search a different cohort, or turn one into a finished video?" (that last option hands off to **creative-director** for a full video, or **video-production** to just cut a clip down).

## Edge cases

- **No Drive access** — if the Drive MCP returns an auth error, tell the user: "I can't reach the Student Interviews Shared Drive right now. Make sure your Google Drive connector is authenticated (Settings → Connectors → Google Drive) and that you have access to the Shared Drive."
- **No matches** — say so explicitly. Suggest broadening: dropping the location filter, trying a different theme, expanding to dimension clips if they only searched highlights, or searching across interview types.
- **Thematic match but no arc** — if a transcript matches the user's query thematically but doesn't contain a narrative (just a flat statement with no setup or turn), skip it. Mention that you found thematically relevant content but nothing with a clear story, and ask if the user wants to see it anyway.
- **Student has only one interview type** — that's fine. A single clip can contain a complete arc. Surface it the same way, just don't try to pair it with interviews that don't exist.
- **Unfamiliar event type slug** — new interview types will appear as new folders. Read the transcripts and infer the interview's purpose from context. Don't skip content just because the slug isn't one you recognize.
- **Ambiguous location** — if "Queens" could match Queens NY *and* a Queens in another state, list both and let the user pick.
- **Student first names only** — when surfacing matches to teammates, use first names (or first + last initial) by default. Don't paste full names + full uids unless the teammate asks for that level of detail.
- **PII / consent** — these clips are for internal storytelling and outreach. Treat the content with care: don't paste long verbatim transcripts back if a short excerpt makes the point, and don't speculate beyond what the transcript says.

## What this skill is NOT for

- Evaluating a student's enrollment readiness (that's the admin app's job).
- Editing, trimming, or rendering clips (that's **video-production**; **creative-director** for a full narrative video — suggest them when the user wants to turn a moment into a video).
- Pulling clips for students who haven't completed any interview yet — if the corpus is empty for a location, say so.
