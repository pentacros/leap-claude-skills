# leap-video — Claude Code plugin marketplace

Internal marketplace for Leap Scholar video tooling.

## Add it (each person, once)

In Claude Code:

    /plugin marketplace add leapfinance/leap-claude-skills
    /plugin install realstitch@leap-video

Updates arrive with `/plugin marketplace update leap-video` — no re-sending files.

## Dependencies (not installed by the plugin)

    brew install ffmpeg
    pip3 install --user --break-system-packages numpy pillow requests
    export ELEVENLABS_API_KEY=...        # forced alignment; never commit this

Check with `python3 .../skills/realstitch/scripts/doctor.py`. Note it does **not**
check for `requests` — install it anyway or stage 1 dies after preflight passes.

**macOS only as shipped.** `overlay.py` hardcodes
`/System/Library/Fonts/HelveticaNeue.ttc` with an Arial fallback. On Linux or in
a container both paths are missing and every caption fails to render, at the
overlay stage rather than at preflight.

## Read before your first run

`skills/realstitch/reference/HANDOFF.md` — seven known bugs in these scripts with
the workaround for each, the frames-first review style, and how to verify keying
so flicker actually gets caught.

## Plugins

| plugin | what |
|---|---|
| `realstitch` | 9:16 reel builder, plus `extend_frame.py` for reshaping 16:9 footage with a supplied still |

## Adding another plugin later

    plugins/<name>/.claude-plugin/plugin.json
    plugins/<name>/skills/<skill>/SKILL.md

then add an entry to `.claude-plugin/marketplace.json` with
`"source": "./plugins/<name>"`.
