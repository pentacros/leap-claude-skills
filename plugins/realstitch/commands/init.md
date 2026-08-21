---
description: First run — set everything up for the user, no terminal required
disable-model-invocation: false
---

The person running this is **not technical**. They will not open a terminal, run
commands, edit config files, or copy shell snippets. You do all of it. Never show
them a command and ask them to run it — run it yourself.

Speak plainly. No `pip3`, no `export`, no filter graphs, no jargon unless they
ask. They need to know three things only: where to put their files, what you
still need from them, and when they'll see something.

Work through this in order.

## 1. Get the machine ready (you, silently)

Run `scripts/doctor.py` yourself.

If something is missing it prints an `--install-cmd:` line. **Do not show that
line to the user.** Say it in plain language — "I need to install a video tool
called ffmpeg, it takes about a minute, shall I?" — and once they agree, run it
yourself and re-check. Ask first: installing software changes their computer and
that is their decision.

## 2. The API key — Aniket sets it up, not the user

`init_project.py` creates a `.env` file in the project folder with an empty
`ELEVENLABS_API_KEY=` line. **Never ask the user for a key, and never ask them to
paste one to you.** Aniket configures it. All they need is the path:

> One thing is still needed before I can read the speech timing: Aniket has to
> add a key to this file — `<absolute path>/.env`. Send him that path and he'll
> set it up. Nothing else is needed from you for it.

Print the absolute path plainly so they can copy it in a message to him. Do not
put a key in the conversation under any circumstances — not theirs, not yours;
it would end up in the transcript.

The scripts read that file directly, so nothing needs exporting and no terminal
is involved. Everything else can be prepared while they wait. Once he has done
it, re-run `doctor.py` to confirm it is found rather than assuming.

## 3. Make the folder and open it for them

Ask what to call the project; suggest `~/Downloads/<name>-reel`. Run
`scripts/init_project.py --dir <path>` yourself, then **open the folder in Finder
for them** (`open <path>`) so they can drag files in rather than typing paths.

Then tell them, in plain sentences, what goes in:

- their video of the person talking
- a background picture — only if the video was shot on a green screen
- the script, one line per row, in `storyboard.csv` (offer to fill this in for
  them if they paste or dictate the script; do not make them edit a spreadsheet
  alone)
- any extra clips or logos the script calls for, into the `Asset` folder

And what they do **not** need to find, because it ships with the plugin: the Leap
outro, the Leap follow button, and the question sticker — that one is drawn
automatically from the first line of their script.

## 4. Wait for them

If anything is missing, stop and say plainly what you're waiting for. Do not
build against the example storyboard, and do not invent a script.

When they say the files are in, re-run `init_project.py` to confirm rather than
taking their word for it — then tell them what you found.

## 5. Set the expectation, then hand over

Before starting, tell them what happens next in one sentence: you'll look at
their video, then **show them a picture of how it will look** and wait for their
yes before making the video itself. Nothing gets rendered before they've seen a
frame.

Then continue with `/realstitch:reel`.
