"""gdh api: look up Godot's class reference for the installed version: a class's members, one member's description,
or a search across every name.

The reference comes from the Godot editor's own help cache (editor_doc_cache-<version>.res), which holds every class's
descriptions. gdh makes it once per Godot version, with a headless editor in gdh's editor home, writes it as JSON to
~/.cache/gdh/api/<version>.json, and reads that after. Nothing of the user's is touched.
"""
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from gdh.godot import HARNESS, GdhError, alert_shims, godot_binary, godot_env, kill_groups

DUMP_SCRIPT = HARNESS / "api_dump.gd"
KIND = {"properties": "property", "methods": "method", "signals": "signal", "constants": "constant",
        "annotations": "annotation", "theme_properties": "theme property"}
SECTIONS = (("properties", "Properties"), ("methods", "Methods"), ("signals", "Signals"),
            ("constants", "Constants"), ("annotations", "Annotations"), ("theme_properties", "Theme properties"))


def cache_root():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return Path(base) / "gdh" / "api"


def godot_version(binary):
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=30).stdout.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GdhError(f"Couldn't run {binary} --version: {e}") from None
    line = out.splitlines()[-1] if out else ""
    if not re.match(r"\d+\.\d+", line):
        raise GdhError(f"{binary} --version printed {out!r}, not a version.")
    return line


def make_doc_cache(binary, minor):
    """Run a headless editor on an empty project until it has written its help cache. Returns the cache's path."""
    from gdh.editor import editor_env, editor_home
    cache = editor_home() / "cache" / "godot" / f"editor_doc_cache-{minor}.res"
    # Remove it, so the editor writes it afresh for this build (it keeps one per minor version).
    cache.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="gdh-api-") as tmp, alert_shims() as shims:
        project = Path(tmp)
        (project / "project.godot").write_text('config_version=5\n\n[application]\n\nconfig/name="gdh api"\n')
        env = editor_env(shims, ":gdh-no-display", [])
        log = open(project / "godot.log", "w")
        proc = subprocess.Popen([binary, "--headless", "--editor", "--path", str(project)], env=env, stdout=log,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
        try:
            deadline = time.monotonic() + 300
            size, steady = -1, 0
            while time.monotonic() < deadline and proc.poll() is None:
                time.sleep(0.5)
                now = cache.stat().st_size if cache.exists() else -1
                steady = steady + 1 if now == size and now > 0 else 0
                size = now
                if steady >= 4:
                    break
        finally:
            kill_groups(proc.pid)
            proc.wait()
            log.close()
    if not cache.exists():
        raise GdhError("The Godot editor didn't write its help cache, so gdh has no class reference to read.")
    return cache


def load_reference(binary):
    """The class reference for this Godot build, as {"godot": ..., "classes": {name: class}}, made once and kept."""
    version = godot_version(binary)
    path = cache_root() / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', version)}.json"
    if not path.exists():
        minor = ".".join(version.split(".")[:2])
        print(f"gdh api: building the class reference for Godot {version} (once per version)...", flush=True,
              file=sys.stderr)
        cache = make_doc_cache(binary, minor)
        path.parent.mkdir(parents=True, exist_ok=True)
        with alert_shims() as shims:
            env = godot_env(shims, ":gdh-no-display", ["--cache", str(cache), "--out", str(path)], game=False)
            proc = subprocess.run([binary, "--headless", "--script", str(DUMP_SCRIPT)], env=env, capture_output=True,
                                  text=True, timeout=300, stdin=subprocess.DEVNULL)
        if proc.returncode != 0 or not path.exists():
            raise GdhError(f"Couldn't read the editor's help cache:\n{(proc.stdout + proc.stderr)[-1500:]}")
    data = json.loads(path.read_text())
    return {"godot": data.get("godot", version), "classes": {c["name"]: c for c in data["classes"]}}


# --- Formatting -------------------------------------------------------------------------------------------------------

def plain(text):
    """Godot's BBCode doc markup as plain text, GDScript examples only."""
    if not text:
        return ""
    text = re.sub(r"\[csharp\].*?\[/csharp\]", "", text, flags=re.S)
    text = re.sub(r"\[/?(?:codeblocks|gdscript)\]", "", text)
    text = re.sub(r"\[codeblock[^\]]*\](.*?)\[/codeblock\]",
                  lambda m: "\n" + "\n".join("    " + line for line in dedent(m.group(1)).splitlines()) + "\n",
                  text, flags=re.S)
    text = re.sub(r"\[(?:method|constructor|operator) ([^\]]+)\]", r"\1()", text)
    text = re.sub(r"\[(?:member|signal|constant|enum|annotation|theme_item|param) ([^\]]+)\]", r"\1", text)
    text = re.sub(r"\[url=[^\]]*\](.*?)\[/url\]", r"\1", text, flags=re.S)
    text = re.sub(r"\[code\](.*?)\[/code\]", r"`\1`", text, flags=re.S)
    text = re.sub(r"\[/?(?:b|i|u|kbd|br|center|lb|rb)\]", "", text)
    text = re.sub(r"\[([A-Z@][A-Za-z0-9_]*)\]", r"\1", text)
    return dedent(text).strip()


def dedent(text):
    lines = [line.rstrip() for line in text.strip("\n").splitlines()]
    indents = [len(line) - len(line.lstrip("\t ")) for line in lines if line.strip()]
    cut = min(indents) if indents else 0
    return "\n".join(line[cut:] for line in lines)


def type_name(entry):
    """An entry's type, naming its enum or bitfield when it has one: `int` alone hides which constants fit."""
    if entry.get("enumeration"):
        return entry["enumeration"]
    return entry.get("type") or "Variant"


def arguments(member):
    out = []
    for a in member.get("arguments", []):
        s = f"{a['name']}: {type_name(a)}"
        if a.get("default_value", "") != "":
            s += f" = {a['default_value']}"
        out.append(s)
    return ", ".join(out)


def signature(kind, m):
    if kind in ("methods", "annotations"):
        quals = f" {m['qualifiers']}" if m.get("qualifiers") else ""
        ret = m.get("return_enum") or m.get("return_type") or "void"
        return f"{m['name']}({arguments(m)}) -> {ret}{quals}"
    if kind == "signals":
        return f"{m['name']}({arguments(m)})"
    if kind == "properties":
        default = f" = {m['default_value']}" if m.get("default_value", "") != "" else ""
        return f"{m['name']}: {type_name(m)}{default}"
    if kind == "constants":
        enum = f"  [{m['enumeration']}]" if m.get("enumeration") else ""
        return f"{m['name']} = {m.get('value', '')}{enum}"
    if kind == "theme_properties":
        return f"{m['name']}: {m.get('type', '')} ({m.get('data_type', '')})"
    return m.get("name", "")


def ancestry(classes, name):
    chain = []
    while name and name in classes and name not in chain:
        chain.append(name)
        name = classes[name].get("inherits", "")
    return chain


def describe_class(ref, name, full):
    classes = ref["classes"]
    c = classes[name]
    chain = ancestry(classes, name)
    lines = [f"{name}" + (f" < {' < '.join(chain[1:])}" if len(chain) > 1 else "") + f"   (Godot {ref['godot']})"]
    if c.get("is_deprecated"):
        lines.append("DEPRECATED. " + plain(c.get("deprecated_message", "")))
    brief = plain(c.get("brief_description", ""))
    if brief:
        lines.append(brief)
    if full and c.get("description"):
        lines += ["", plain(c["description"])]
    for key, title in SECTIONS:
        members = c.get(key, [])
        if not members:
            continue
        lines += ["", f"{title}:"]
        for m in members:
            flag = "  (deprecated)" if m.get("is_deprecated") else ""
            lines.append(f"  {signature(key, m)}{flag}")
    if len(chain) > 1:
        lines += ["", f"Inherited members: gdh api {chain[1]}   (or gdh api {name}.MEMBER searches the chain)"]
    return "\n".join(lines)


def find_member(ref, name, member):
    """[(class, kind, member)] for member in name's class or its ancestors (the nearest first)."""
    found = []
    for cls in ancestry(ref["classes"], name):
        c = ref["classes"][cls]
        for key, _ in SECTIONS:
            for m in c.get(key, []):
                if m.get("name") == member:
                    found.append((cls, key, m))
        if found:
            return found
    return found


def describe_member(ref, name, member):
    found = find_member(ref, name, member)
    if not found:
        names = {m.get("name") for cls in ancestry(ref["classes"], name) for key, _ in SECTIONS
                 for m in ref["classes"][cls].get(key, [])}
        close = difflib.get_close_matches(member, sorted(n for n in names if n), n=6, cutoff=0.5)
        hint = f" Close: {', '.join(close)}." if close else ""
        raise GdhError(f"{name} has no member {member}, nor do the classes it inherits from (Godot {ref['godot']}).{hint}")
    out = []
    for cls, kind, m in found:
        where = "" if cls == name else f"   (from {cls})"
        out.append(f"{cls}.{signature(kind, m)}   [{KIND[kind]}]{where}")
        if m.get("is_deprecated"):
            out.append("DEPRECATED. " + plain(m.get("deprecated_message", "")))
        if m.get("setter") or m.get("getter"):
            out.append(f"setter: {m.get('setter') or '-'}, getter: {m.get('getter') or '-'}")
        desc = plain(m.get("description", ""))
        out.append(desc or "(no description)")
        out.append("")
    return "\n".join(out).rstrip()


def search(ref, text, limit):
    text = text.lower()
    hits = []
    for name, c in sorted(ref["classes"].items()):
        if text in name.lower():
            hits.append(f"{name}   [class] {plain(c.get('brief_description', ''))[:90]}")
        for key, _ in SECTIONS:
            for m in c.get(key, []):
                if text in m.get("name", "").lower():
                    hits.append(f"{name}.{signature(key, m)}   [{KIND[key]}]")
    shown = hits[:limit]
    more = f"\n... {len(hits) - limit} more: narrow the search" if len(hits) > limit else ""
    return ("\n".join(shown) + more) if hits else f"Nothing in Godot {ref['godot']} is named like {text!r}."


def resolve_class(ref, name):
    if name in ref["classes"]:
        return name
    lower = {n.lower(): n for n in ref["classes"]}
    if name.lower() in lower:
        return lower[name.lower()]
    close = difflib.get_close_matches(name, list(ref["classes"]), n=6, cutoff=0.6)
    hint = f" Close: {', '.join(close)}." if close else " Try gdh api --search."
    raise GdhError(f"Godot {ref['godot']} has no class {name}.{hint}")


def cmd_api(args):
    binary = godot_binary(args.project) if args.project else os.environ.get("GODOT", "godot")
    if not shutil.which(binary) and not Path(binary).exists():
        raise GdhError(f"{binary} isn't on PATH: install Godot or set GODOT.")
    ref = load_reference(binary)
    if args.search:
        print(search(ref, args.search, args.limit))
        return 0
    if not args.name:
        raise GdhError("Give a class (gdh api Node2D), a member (gdh api Node2D.position) or --search TEXT.")
    cls, _, member = args.name.partition(".")
    cls = resolve_class(ref, cls)
    if args.json:
        c = ref["classes"][cls]
        print(json.dumps([m for _, _, m in find_member(ref, cls, member)] if member else c, indent=2))
        return 0
    print(describe_member(ref, cls, member) if member else describe_class(ref, cls, args.full))
    return 0


def add_parser(sub):
    p = sub.add_parser("api", help="Look up Godot's class reference for the installed version")
    p.add_argument("name", nargs="?", help="A class (Node2D), or a class and member (Node2D.position, @GDScript.preload)")
    p.add_argument("--search", metavar="TEXT", help="Every class and member whose name contains TEXT")
    p.add_argument("--full", action="store_true", help="Also print the class's long description")
    p.add_argument("--limit", type=int, default=60, help="Most search results to print (default 60)")
    p.add_argument("--project", help="Use the Godot this project runs with (godot-mono for a C# project)")
    p.add_argument("--json", action="store_true", help="Print the reference's raw entry")
    p.set_defaults(func=cmd_api)
