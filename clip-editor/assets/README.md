# Audio assets

Music tracks used by `stitch_clips` when scoring is requested.

| File | Used when |
|---|---|
| `rising action.mp3` | Plays under the struggle/rising-action portion of the narrative |
| `triumph.mp3` | Plays under the triumph portion (after the turning-point pause) |

The MCP server reads these from `clip-editor/assets/` at render time. They're baked into the Docker image at build time, so add new tracks here and redeploy.

Both filenames are referenced verbatim by the server. Renaming a file or removing one will fail `stitch_clips` calls that request scoring. Override paths via env vars (`RISING_ACTION_PATH`, `TRIUMPH_PATH`) if needed.

Tracks are looped and trimmed to fit the timeline — no manual length-matching needed.
