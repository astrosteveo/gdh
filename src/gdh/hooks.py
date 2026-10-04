"""Claude Code hooks that keep Claude's file edits in step with the Godot editor and its UIDs.

pre  (PreToolUse on Write, Edit and MultiEdit) refuses an edit that would fight the editor or break references:
     - anything in .godot/, the editor's cache;
     - project.godot while an editor runs the bridge (the editor rewrites it from memory);
     - a scene the editor has unsaved changes in (the edit would be lost, or the user's would);
     - a uid:// the project doesn't have (a made-up UID points at nothing, or at the wrong file).
post (PostToolUse) tells the editor about the change: rescans the file, reloads an open scene, has Godot fill in a new
     scene's or resource's UIDs, and checks a GDScript, returning its errors to Claude.

With no bridge running it still refuses .godot/ and made-up UIDs, and checks GDScript with a headless parse.
GDH_HOOKS=off turns the hooks off. Only the standard library is used.
"""
import json
import os
import re
import sys
from pathlib import Path

from gdh import editor_bridge as eb

UID_RE = re.compile(r"uid://[0-9a-z]+")
HEADER_UID_RE = re.compile(r'^\[gd_(?:scene|resource)[^\]]*\buid="(uid://[0-9a-z]+)"')
TEXT_RESOURCES = {".tscn", ".tres"}
EDIT_TOOLS = {"Write", "Edit", "MultiEdit"}


def edit_texts(tool, tool_input, path):
    """(new text, old text) of an edit: what it adds and what it replaces."""
    if tool == "Write":
        try:
            old = path.read_text(errors="replace")
        except OSError:
            old = ""
        return tool_input.get("content", ""), old
    if tool == "Edit":
        return tool_input.get("new_string", ""), tool_input.get("old_string", "")
    edits = tool_input.get("edits", [])
    return "\n".join(e.get("new_string", "") for e in edits), "\n".join(e.get("old_string", "") for e in edits)


def known_uids_on_disk(project):
    """Every UID the project's files declare: .uid sidecars, .import files and the headers of text scenes and
    resources."""
    found = set()
    for path in project_text_files(project):
        try:
            if path.suffix == ".uid":
                found.update(UID_RE.findall(path.read_text(errors="replace")))
            elif path.suffix == ".import":
                m = re.search(r'^uid="(uid://[0-9a-z]+)"', path.read_text(errors="replace"), re.M)
                if m:
                    found.add(m.group(1))
            elif path.suffix in TEXT_RESOURCES:
                with open(path, errors="replace") as f:
                    m = HEADER_UID_RE.match(f.readline())
                if m:
                    found.add(m.group(1))
        except OSError:
            continue
    return found


def project_text_files(project):
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for name in files:
            if name.endswith((".uid", ".import", ".tscn", ".tres")):
                yield Path(root) / name


def unknown_uids(project, uids, bridge_up):
    """The UIDs among uids that the project doesn't have."""
    uids = sorted(uids)
    if bridge_up:
        try:
            reply = eb.call(project, {"cmd": "uid", "items": uids}, timeout=10)
            return [u for u, path in reply.get("uids", {}).items() if path is None]
        except eb.NoBridge:
            pass
    known = known_uids_on_disk(project)
    return [u for u in uids if u not in known]


def deny(reason):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                             "permissionDecisionReason": reason}}))
    return 0


def pre(event):
    tool_input = event.get("tool_input", {})
    path = Path(tool_input.get("file_path", ""))
    project = eb.find_project(path)
    if project is None:
        return 0
    rel = path.resolve().relative_to(project) if path.resolve().is_relative_to(project) else None
    if rel is None:
        return 0
    if rel.parts and rel.parts[0] == ".godot":
        return deny(f"{rel} is in .godot/, the Godot editor's cache. Never edit it: change the project's own files, "
                    f"and let the editor rebuild its cache (gdh bridge scan).")
    status = eb.status(project, timeout=3)
    res = "res://" + rel.as_posix()
    if status:
        if rel.as_posix() == "project.godot":
            return deny("The Godot editor is open on this project and rewrites project.godot from memory, so an edit "
                        "on disk would be lost. Change settings inside the editor with gdh bridge exec "
                        "(ProjectSettings.set_setting(...), then ProjectSettings.save()), or ask the user to.")
        if res in status.get("unsaved_scenes", []):
            return deny(f"{res} is open in the Godot editor with unsaved changes. Writing it on disk would lose the "
                        f"user's changes or yours. Edit it live through the editor with gdh bridge exec (on the current "
                        f"scene: gdh bridge open {res} first), or ask the user to save it.")
    new, old = edit_texts(event.get("tool_name"), tool_input, path)
    added = set(UID_RE.findall(new)) - set(UID_RE.findall(old))
    if added:
        bad = unknown_uids(project, added, bool(status))
        if bad:
            return deny(f"{', '.join(bad)} {'is not a UID' if len(bad) == 1 else 'are not UIDs'} this project has. "
                        f"Never make up a UID: leave uid=\"...\" out (path= alone works, and the header's uid is "
                        f"optional), then let Godot fill it in (gdh bridge resave PATH, or the editor's next save). "
                        f"To find a real one: gdh bridge uid res://path, the file's .uid sidecar, or its .import file.")
    return 0


def post(event):
    tool_input = event.get("tool_input", {})
    path = Path(tool_input.get("file_path", ""))
    project = eb.find_project(path)
    if project is None or not path.exists():
        return 0
    if not path.resolve().is_relative_to(project):
        return 0
    rel = path.resolve().relative_to(project)
    if rel.parts and rel.parts[0] in (".godot", "addons") and "gdh_bridge" in rel.parts:
        return 0
    res = "res://" + rel.as_posix()
    notes = []
    problems = []
    status = eb.status(project, timeout=3)
    if status:
        try:
            notes += editor_post(project, path, res, status, problems)
        except eb.NoBridge as e:
            notes.append(f"The editor bridge stopped answering: {e}")
    elif path.suffix == ".gd":
        for e in eb.check_headless(project, [res]).get(res, []):
            if e["type"] != "warning":
                problems.append(f"{e['type']}: {e['message']} at {e['where']}")
    if path.suffix in TEXT_RESOURCES and not status and not has_header_uid(path):
        notes.append(f"{res} has no UID yet and no editor is running to give it one. Godot's next save of it fills it "
                     f"in; to do it now: gdh bridge start, then gdh bridge resave {res}.")
    if problems:
        print(json.dumps({"decision": "block", "reason": f"Godot reports errors in {res}:\n" + "\n".join(problems[:20])
                          + ("\n" + "\n".join(notes) if notes else "")}))
    elif notes:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(notes)}}))
    return 0


def has_header_uid(path):
    try:
        with open(path, errors="replace") as f:
            return bool(HEADER_UID_RE.match(f.readline()))
    except OSError:
        return False


def editor_post(project, path, res, status, problems):
    """Tell the running editor about a changed file. Returns notes for Claude; appends errors to problems."""
    notes = []
    reply = eb.call(project, {"cmd": "scan", "paths": [res]})
    editor_errors = list(reply.get("errors", []))
    if path.suffix == ".gd":
        reply = eb.call(project, {"cmd": "check", "paths": [res]})
        for e in reply.get("checked", {}).get(res, []):
            if e["type"] != "warning":
                problems.append(f"{e['type']}: {e['message']} at {e['where']}")
    elif path.suffix in TEXT_RESOURCES | {".scn"}:
        is_open = res in status.get("open_scenes", [])
        if not has_header_uid(path) and path.suffix in TEXT_RESOURCES:
            reply = eb.call(project, {"cmd": "resave", "paths": [res]})
            editor_errors += reply.get("errors", [])
            uid = reply.get("resaved", {}).get(res)
            if uid:
                notes.append(f"Godot saved {res} again to fill in its UID ({uid}) and its ext_resource UIDs. The file "
                             f"changed on disk: read it again before editing it.")
            else:
                notes.append(f"Godot couldn't fill in {res}'s UIDs: {reply.get('failed', {}).get(res, 'unknown')}")
        elif is_open:
            reply = eb.call(project, {"cmd": "reload", "paths": [res]})
            editor_errors += reply.get("errors", [])
            if res in reply.get("reloaded", []):
                notes.append(f"Reloaded {res} in the editor, so the user sees the change.")
    for e in editor_errors:
        if e["type"] != "warning":
            problems.append(f"editor {e['type']}: {e['message']} at {e['where']}")
    return notes


def main(argv):
    if os.environ.get("GDH_HOOKS", "").lower() in ("off", "0", "false"):
        return 0
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    if event.get("tool_name") not in EDIT_TOOLS:
        return 0
    try:
        return pre(event) if argv[:1] == ["pre"] else post(event)
    except eb.GdhError as e:
        print(f"gdh hook: {e}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
