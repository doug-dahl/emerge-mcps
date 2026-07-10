# Set up the video editor on your Mac

This makes Claude able to edit videos (cut, stitch, caption, brand) right on your
own computer, through the **Claude Desktop** app. The video work happens locally,
so there's no server to break.

## Setup (double-click, ~2 minutes)

You need the **Claude Desktop** app installed ([claude.ai/download](https://claude.ai/download)).

1. Unzip the **clip-editor** folder someone shared with you.
2. Double-click **`Set Up clip-editor.command`** inside it.
   - First time: macOS may say it "can't verify the developer." If so, **right-click
     the file → Open → Open**. (You only do this once.)
3. A Terminal window runs the setup. It downloads everything it needs — you don't
   have to install anything yourself.
4. When it asks, **paste the access key** (a teammate will give it to you) and click OK.
5. When it says done, **fully quit and reopen Claude Desktop.**

That's it. You never touch setup again.

## Using it

In Claude Desktop, just ask — for example:

- "Find a story from a student in Queens and make a 30-second vertical reel with captions."
- "Stitch these three clips into a one-minute video with music and our logo."
- "Cut the interviewer out of this clip."

Finished videos are saved to **`~/Movies/EmergeClips`**, and Claude gives you the
file to open or share.

## If something's off

- **"clip-editor" tools missing?** Quit and reopen Claude Desktop. If still missing,
  double-click **Set Up clip-editor.command** again.
- **"Can't reach the drive" / auth error?** The access key is missing or wrong —
  re-run the setup and paste it again when prompted.

## Note

This runs in **Claude Desktop** (or Claude Code). It does **not** work in a web
browser — the browser can't run the local editor.
