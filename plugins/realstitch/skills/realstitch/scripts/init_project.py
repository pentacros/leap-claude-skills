#!/usr/bin/env python3
"""Scaffold a reel project folder and report what is still missing.

A fresh user has none of the folder layout this pipeline assumes, and the scripts
fail late and unhelpfully when something is absent - storyboard.py errors on an
unresolved asset, overlay.py raises if the follow button is missing. This creates
the layout, links the bundled brand assets, writes a storyboard template, then
says plainly what it found and what you still owe it.

    python3 init_project.py --dir ~/Downloads/my-reel
"""
import argparse
import csv
import os
import sys

FOOTAGE_EXT = (".mp4", ".mov", ".m4v")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp")
SUBDIRS = ("Asset", "work", "output")
STORYBOARD_HEADER = ("script_line", "visual", "notes")
STORYBOARD_EXAMPLE = [
    ("Replace this with the first line of your script, verbatim.",
     "IG QnA Button template with the question",
     "Question Overlay all over the video; Subtitles through out the video"),
    ("The second line of your script.", "NA", ""),
    ("A line where you want logos to pop in.",
     "pop up style element of harvard, nyu, columbia college logos.", ""),
    ("A line where one term should be highlighted.", "highlight: YOUR TERM", ""),
    ("A line that should list points.",
     "Put in bullet points: First, Second, Third.", ""),
]
ENV_TEMPLATE = ("# Paste the ElevenLabs key after the = sign, save, and close.\n"
                "# Ask Aniket (aniket.rajput@leapfinance.com) if you do not have it.\n"
                "# This file stays on your computer. Never share or commit it.\n"
                "ELEVENLABS_API_KEY=\n")


def find(dirpath, exts):
    if not os.path.isdir(dirpath):
        return []
    return sorted(f for f in os.listdir(dirpath)
                  if f.lower().endswith(exts) and not f.startswith("."))


def bundled_brand():
    """assets/brand inside the installed skill, wherever that happens to be."""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(os.path.dirname(here), "assets", "brand")
    return cand if os.path.isdir(cand) else None


def link_brand(project):
    src = bundled_brand()
    dst = os.path.join(project, "brand")
    if not src:
        return None, "bundled brand assets not found next to the scripts"
    if os.path.islink(dst) or os.path.exists(dst):
        return dst, "already present"
    try:
        os.symlink(src, dst)                     # link, not copy: the assets are ~25MB
        return dst, "linked to the bundled assets"
    except OSError as exc:
        return None, f"could not link ({exc})"


def write_storyboard(project):
    path = os.path.join(project, "storyboard.csv")
    if os.path.exists(path):
        return path, "already present, left alone"
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(STORYBOARD_HEADER)
        w.writerows(STORYBOARD_EXAMPLE)
    return path, "template written - replace every row with your own script"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default=".", help="project folder (created if absent)")
    a = ap.parse_args()
    project = os.path.abspath(os.path.expanduser(a.dir))
    os.makedirs(project, exist_ok=True)
    for sub in SUBDIRS:
        os.makedirs(os.path.join(project, sub), exist_ok=True)

    brand, brand_note = link_brand(project)
    sb, sb_note = write_storyboard(project)
    env = os.path.join(project, ".env")
    env_new = not os.path.exists(env)
    if env_new:
        with open(env, "w", encoding="utf-8") as fh:
            fh.write(ENV_TEMPLATE)

    footage = find(project, FOOTAGE_EXT)
    images = find(project, IMAGE_EXT)
    assets = find(os.path.join(project, "Asset"), FOOTAGE_EXT + IMAGE_EXT)
    with open(sb, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if (r.get("script_line") or "").strip()]
    template_still = any("Replace this with" in (r["script_line"] or "") for r in rows)

    print("=" * 72)
    print(f"PROJECT FOLDER:  {project}")
    print("=" * 72)
    print("Put your files here:")
    print(f"  {os.path.join(project, '<your-footage>.mp4'):<58} your talking-head video")
    print(f"  {os.path.join(project, '<background>.jpg'):<58} background (green-screen footage only)")
    print(f"  {os.path.join(project, 'storyboard.csv'):<58} one row per line of your script")
    print(f"  {os.path.join(project, 'Asset') + '/':<58} b-roll clips + logos your storyboard names")
    print()
    print("Written for you - do not edit:")
    print(f"  {os.path.join(project, 'brand') + '/':<58} Leap outro + follow button (symlink, ~25MB)")
    print(f"  {os.path.join(project, 'work') + '/':<58} intermediates; safe to delete")
    print(f"  {os.path.join(project, 'output') + '/':<58} finished reel, report, captions")
    print("=" * 72)
    print()
    print(f"  folders      {', '.join(SUBDIRS)}")
    print(f"  brand/       {brand_note}")
    print(f"  storyboard   {sb_note}")
    print(f"  .env         {'created - paste the key into it' if env_new else 'already present'}")
    print()
    print("SHIPPED WITH THE PLUGIN - you do not need to find or place these:")
    print("  - Leap outro slate")
    print("  - Leap follow-button animation")
    print("  - the question sticker, now DRAWN from your storyboard's first line")
    print()
    print("YOU STILL NEED:")
    ok = True
    if footage:
        print(f"  [ok]      footage: {', '.join(footage)}")
    else:
        print(f"  [MISSING] footage - drop your talking-head file in {project}")
        ok = False
    if images:
        print(f"  [ok]      background: {', '.join(images)}")
    else:
        print("  [MISSING] background image - only needed for green-screen footage")
    if rows and not template_still:
        print(f"  [ok]      storyboard: {len(rows)} rows")
    else:
        print("  [MISSING] storyboard rows - storyboard.csv still holds the template")
        ok = False
    if assets:
        print(f"  [ok]      Asset/: {len(assets)} files ({', '.join(assets[:4])}"
              f"{', ...' if len(assets) > 4 else ''})")
    else:
        print("  [note]    Asset/ is empty - b-roll clips and logos are per-topic. "
              "Any storyboard row naming one will go unresolved until you add it.")
    print()
    import envfile
    if not envfile.load("ELEVENLABS_API_KEY", project):
        print("API KEY: not set yet.")
        print(f"  Open this file and paste the key after the = sign, then save:")
        print(f"    {env}")
        print("  Ask Aniket (aniket.rajput@leapfinance.com) if you do not have it.")
        print("  It stays on this computer. About $0.12 per reel.")
        ok = False
    else:
        print("API KEY: found.")
    print()
    print("READY - ask for the build" if ok
          else "NOT READY - add the items marked MISSING above, then re-run this")
    return 0


if __name__ == "__main__":
    sys.exit(main())
