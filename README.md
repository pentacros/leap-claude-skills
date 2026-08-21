# leap-video — Claude Code plugin marketplace

Internal marketplace for Leap Scholar video tooling.

## Add it (each person, once)

In Claude Code:

    /plugin marketplace add pentacros/leap-claude-skills
    /plugin install realstitch@leap-video

Updates arrive with `/plugin marketplace update leap-video` — no re-sending files.

## Dependencies (not installed by the plugin)

    brew install ffmpeg
    pip3 install --user --break-system-packages numpy pillow requests
Then paste the ElevenLabs key into the project's `.env` file (created by
`/realstitch:init`). The scripts read it from there - nothing to export.

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

### What `realstitch` gives you

| component | name | does |
|---|---|---|
| skill | `realstitch` | the pipeline, plus four reference docs of measured findings |
| command | `/realstitch:reel` | frames-first build: measure, render frames, wait for approval, then build |
| agent | `reel-reviewer` | read-only check of a delivered reel against the gate, keying churn and delivery spec |
| hook | `Stop` | refuses to finish while the newest build report shows a safe-zone FAILURE |

The hook reads only `output/build-report*.md` and fails open — no reports, an
ungated report, or any error and it stays silent.

## Adding another plugin later

    plugins/<name>/.claude-plugin/plugin.json
    plugins/<name>/skills/<skill>/SKILL.md

then add an entry to `.claude-plugin/marketplace.json` with
`"source": "./plugins/<name>"`.
