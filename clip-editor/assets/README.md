# Audio assets

Royalty-free music used by `stitch_clips`. All tracks live in
[`music/`](music/) and are baked into the Docker image at build time — add a
track there and redeploy to make it available.

Every bundled track is by **Kevin MacLeod (incompetech.com)**, licensed
**Creative Commons: By Attribution 4.0** — see [`music/CREDITS.md`](music/CREDITS.md)
for the per-file title and the attribution line to include when publishing.

| Track | Mood | Used by |
|---|---|---|
| `music/cinematic.mp3` | building, emotional | default **rising-action** phase of the two-act `music` score |
| `music/uplifting.mp3` | bright, hopeful | default **triumph** phase of the two-act `music` score |
| `music/hopeful.mp3` | slow, emotional | `music_bed` option |
| `music/calm.mp3` | gentle, reflective | `music_bed` option |

## Two ways to score a video

- **`music` (two-act score)** — rising-action → optional silent turning point →
  triumph. Tracks are looped and trimmed to fit. Override the two slots with the
  `RISING_ACTION_PATH` / `TRIUMPH_PATH` env vars.
- **`music_bed` (single continuous bed)** — one track under the whole video with
  a gentle fade in/out; the better fit for sensitive/documentary stories. Pass a
  bundled bed name (`hopeful`, `calm`, `cinematic`, `uplifting`) or a path to
  your own file. Mutually exclusive with `music`.

## Brand

`brand/emerge-logo-light.png` — the Emerge Career wordmark (red mark + white
text) for the dark title/closing slates. `stitch_clips`'s `intro` / `outro`
slates show it by default (`"logo": false` to hide). Drop a replacement here to
rebrand; slates still render if it's missing (logo is just skipped).
