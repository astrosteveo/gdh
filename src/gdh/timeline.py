"""A live session's timeline: every command sent to the game, one after another, with what it sent, the frames it ran,
how long it took, the engine errors, notes and game output that came back, a thumbnail of the frame after it, and the
shots it saved. `gdh live start --timeline` keeps one in <out>/timeline/, from every way in (a command, batch, pipe, a
recipe, a replay), so a playtest can be looked through afterwards, as Playwright's trace viewer does for a browser.

The directory holds index.html, a page that opens from the disk with no server, and timeline.js beside it, one line a
command (`T(...);`), which the page loads: written as the session goes, so the page is current at any time, a crashed
session's included. A restart adds to it: the old game's stop, then a mark where the game started again; a new start begins it afresh.
"""
import json
import os
import sys
import time
from pathlib import Path

DIR_NAME = "timeline"
DATA_NAME = "timeline.js"
# The commands whose frame a thumbnail shows afterwards: the ones that move the game on or change what it shows.
THUMBNAILED = ("step", "run", "pause", "camera", "reload")
THUMB_WIDTH = 320
# The commands left out: status is asked for all the time and changes nothing.
SKIPPED = ("status", "quit")
# A result longer than this, as JSON, is kept cut (a whole scene tree).
RESULT_CHARS = 6000


def begin(args, session, restarted=False):
    """Start the session's timeline when it was started with --timeline: a new one, or, after a restart, the one it
    had, with a mark where it started again."""
    if not getattr(args, "timeline", False):
        return
    folder = Path(session["out"]) / DIR_NAME
    data = folder / DATA_NAME
    (folder / "thumbs").mkdir(parents=True, exist_ok=True)
    if not restarted:
        for old in (folder / "thumbs").glob("*.png"):
            old.unlink()
        data.write_text("")
    (folder / "index.html").write_text(PAGE)
    session["timeline"] = str(folder)
    _append(session, {"cmd": "restart" if restarted else "start", "time": time.time(), "session": session["name"],
                      "project": session["project"], "scene": session.get("scene", ""), "seed": session.get("seed"),
                      "instances": len(session.get("instances", [])), "frame": 0, "ok": True})
    print(f"timeline: {folder / 'index.html'}")


def record(session, cmd, args, instance, replies, games, started, request):
    """Add a command and its replies to the session's timeline, if it keeps one, with a thumbnail of each instance
    the command moved on. `games` are the instances that answered, in the replies' order; `request(index, cmd, args)`
    asks an instance for something without logging it."""
    folder = session.get("timeline")
    if not folder or cmd in SKIPPED:
        return
    folder = Path(folder)
    entry = {"cmd": cmd, "args": args, "instance": instance, "time": started, "ms": round((time.time() - started) * 1000),
             "ok": all(r.get("ok") for r in replies)}
    many = len(replies) > 1
    parts = []
    for game, reply in zip(games, replies):
        part = {"frame": reply.get("frame")}
        if many:
            part["instance"] = game
        for key in ("error", "errors", "notes", "output"):
            if reply.get(key):
                part[key] = reply[key]
        if "result" in reply:
            part["result"] = _cut(reply["result"])
            part["shots"] = [_rel(p, folder) for p in _shots(reply["result"])]
        parts.append(part)
    entry["replies"] = parts
    if cmd == "step" and entry["ok"]:
        entry["ran"] = replies[0].get("result", {}).get("frames")
    if cmd in THUMBNAILED and entry["ok"]:
        number = _count(folder)
        thumbs = []
        for game in games:
            path = folder / "thumbs" / f"{number:05d}{f'-i{game}' if many else ''}.png"
            try:
                shot = request(game, "shot", {"label": "timeline", "out": str(path), "max_width": THUMB_WIDTH})
            except Exception:  # a game that stopped answering: the entry stays, without its picture
                shot = {}
            if shot.get("ok"):
                thumbs.append(_rel(path, folder))
        entry["thumbs"] = thumbs
    _append(session, entry)


def end(session):
    """Mark the session's end on its timeline."""
    if session.get("timeline"):
        _append(session, {"cmd": "stop", "time": time.time(), "ok": True})


def _append(session, entry):
    path = Path(session["timeline"]) / DATA_NAME
    try:
        with open(path, "a") as f:
            f.write(f"T({json.dumps(entry)});\n")
    except OSError as e:
        print(f"note: can't add to the timeline {path}: {e}", file=sys.stderr)


def _count(folder):
    try:
        with open(folder / DATA_NAME, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _shots(result):
    """The image files a result names: a step's and a probe's shots, a shot's views."""
    found = []
    shots = result.get("shots") if isinstance(result, dict) else None
    if isinstance(shots, list):
        found += [p for p in shots if isinstance(p, str)]
    elif isinstance(shots, dict):
        found += [p for p in shots.values() if isinstance(p, str)]
    return found


def _rel(path, folder):
    try:
        return os.path.relpath(path, folder)
    except ValueError:
        return str(path)


def _cut(result):
    text = json.dumps(result)
    if len(text) <= RESULT_CHARS:
        return result
    return {"cut": text[:RESULT_CHARS], "chars": len(text)}


PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>gdh live timeline</title>
<style>
  :root { color-scheme: light dark; --fg: #1d1f23; --muted: #6b7079; --line: #d9dce1; --bg: #fff; --row: #f6f7f9;
          --err: #c4302b; --note: #a86b00; --ok: #2f7d32; --code: #eef0f3; }
  @media (prefers-color-scheme: dark) {
    :root { --fg: #e4e6ea; --muted: #9298a2; --line: #33373e; --bg: #16181c; --row: #1d2025; --err: #ff6b63;
            --note: #e5a83a; --ok: #6cc070; --code: #262a31; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 system-ui, sans-serif; color: var(--fg); background: var(--bg); }
  header { position: sticky; top: 0; z-index: 2; background: var(--bg); border-bottom: 1px solid var(--line);
           padding: 10px 16px; display: flex; gap: 16px; align-items: baseline; flex-wrap: wrap; }
  header h1 { font-size: 15px; margin: 0; }
  header .sum { color: var(--muted); }
  header label { color: var(--muted); user-select: none; }
  main { padding: 8px 16px 40px; }
  .entry { display: grid; grid-template-columns: 52px 168px 1fr; gap: 12px; padding: 10px 8px;
           border-bottom: 1px solid var(--line); }
  .entry:nth-child(even) { background: var(--row); }
  .entry.bad { box-shadow: inset 3px 0 var(--err); }
  .entry.mark { background: transparent; color: var(--muted); grid-template-columns: 52px 1fr; }
  .n { color: var(--muted); font-variant-numeric: tabular-nums; text-align: right; }
  .n small { display: block; }
  .thumbs img { width: 160px; display: block; border: 1px solid var(--line); cursor: zoom-in; margin-bottom: 4px;
                image-rendering: auto; }
  .cmd { font: 13px ui-monospace, monospace; word-break: break-word; }
  .cmd b { font-weight: 600; }
  .meta { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .err { color: var(--err); font: 12px ui-monospace, monospace; white-space: pre-wrap; margin-top: 4px; }
  .note { color: var(--note); font-size: 13px; margin-top: 4px; }
  .out { color: var(--muted); font: 12px ui-monospace, monospace; white-space: pre-wrap; margin-top: 4px; }
  .shots a { margin-right: 8px; font-size: 12px; }
  details { margin-top: 4px; }
  details summary { color: var(--muted); font-size: 12px; cursor: pointer; }
  pre { background: var(--code); padding: 8px; overflow: auto; max-height: 320px; font-size: 12px; margin: 4px 0 0; }
  #zoom { position: fixed; inset: 0; background: rgba(0,0,0,.8); display: none; align-items: center;
          justify-content: center; z-index: 3; cursor: zoom-out; }
  #zoom img { max-width: 96vw; max-height: 96vh; }
  .empty { color: var(--muted); padding: 24px 8px; }
</style>
</head>
<body>
<header>
  <h1 id="title">gdh live timeline</h1>
  <span class="sum" id="sum"></span>
  <label><input type="checkbox" id="only"> only commands with errors or notes</label>
</header>
<main id="list"><div class="empty">No commands yet.</div></main>
<div id="zoom"><img alt=""></div>
<script>
const ENTRIES = [];
function T(e) { ENTRIES.push(e); }
</script>
<script src="timeline.js"></script>
<script>
const $ = (tag, cls, text) => { const el = document.createElement(tag); if (cls) el.className = cls;
  if (text !== undefined) el.textContent = text; return el; };

function argsText(cmd, a) {
  if (!a) return "";
  if (cmd === "eval") return a.expr || "";
  if (cmd === "step") {
    const bits = [String(a.frames ?? 1)];
    for (const e of a.events || []) {
      if (e.pressed === false || e.mouse_motion) continue;
      const at = e.at ? `@${e.at} ` : "";
      if (e.on) bits.push(`${at}click ${e.on.text !== undefined ? JSON.stringify(e.on.text) : e.on.node}`);
      else if (e.mouse_button) bits.push(`${at}mouse${e.mouse_button} ${e.position ?? ""}`);
      else if (e.action) bits.push(`${at}${e.action}`);
      else if (e.key) bits.push(`${at}key:${e.key}`);
      else if (e.text) bits.push(`${at}type ${JSON.stringify(e.text)}`);
      else if (e.touch) bits.push(`${at}touch ${e.touch}`);
    }
    if (a.until) bits.push(`--until ${a.until}`);
    for (const t of a.trace || []) bits.push(`--trace ${t}`);
    return bits.join("  ");
  }
  const s = JSON.stringify(a);
  return s === "{}" ? "" : s;
}

function render() {
  const only = document.getElementById("only").checked;
  const list = document.getElementById("list");
  list.replaceChildren();
  const t0 = ENTRIES.length ? ENTRIES[0].time : 0;
  let errors = 0, failed = 0, frame = 0, n = 0;
  for (const e of ENTRIES) {
    const replies = e.replies || [];
    const errs = replies.flatMap(r => r.errors || []);
    const notes = replies.flatMap(r => r.notes || []);
    errors += errs.filter(x => x.type !== "warning").length;
    if (!e.ok) failed++;
    const before = frame;
    const after = replies.length ? replies[0].frame ?? frame : (e.frame ?? frame);
    frame = after;
    if (e.cmd === "start" || e.cmd === "restart" || e.cmd === "stop") {
      frame = e.cmd === "stop" ? frame : 0;
      if (only) continue;
      const row = $("div", "entry mark");
      row.append($("div", "n", ""), $("div", "",
        e.cmd === "stop" ? `stopped (+${(e.time - t0).toFixed(1)} s)`
          : `${e.cmd === "start" ? "started" : "started again"}: ${e.scene || ""}  seed ${e.seed ?? "-"}` +
            (e.instances > 1 ? `  ${e.instances} instances` : "") + (e.cmd === "start" ? `  ${e.project}` : "")));
      list.append(row);
      continue;
    }
    n++;
    if (only && e.ok && !errs.length && !notes.length) continue;
    const row = $("div", "entry" + (!e.ok || errs.some(x => x.type !== "warning") ? " bad" : ""));
    const num = $("div", "n", String(n));
    num.append($("small", "", `+${(e.time - t0).toFixed(1)}s`));
    const thumbs = $("div", "thumbs");
    // A thumbnail after a command that moved the game on; a shot's own image for a shot.
    const pictures = e.thumbs && e.thumbs.length ? e.thumbs
      : replies.flatMap(r => r.shots || []).filter(p => p.endsWith(".png")).slice(0, 1);
    for (const src of pictures) {
      const img = $("img"); img.src = src; img.loading = "lazy"; img.alt = `frame ${after}`; thumbs.append(img);
    }
    const body = $("div");
    const cmd = $("div", "cmd"); cmd.append($("b", "", e.cmd), " " + argsText(e.cmd, e.args));
    if (e.instance !== undefined && String(e.instance) !== "0") cmd.append(`  (instance ${e.instance})`);
    body.append(cmd);
    const meta = [`frame ${before} → ${after}`];
    if (e.ran !== undefined && e.ran !== null) meta.push(`ran ${e.ran}`);
    meta.push(`${e.ms} ms`);
    body.append($("div", "meta", meta.join(" · ")));
    for (const r of replies) {
      const pre = r.instance !== undefined ? `[${r.instance}] ` : "";
      if (r.error) body.append($("div", "err", pre + r.error));
      for (const x of r.errors || []) {
        const trace = (x.backtrace || []).map(f => `\n  at ${f}`).join("");
        body.append($("div", x.type === "warning" ? "note" : "err",
          `${pre}${x.type}: ${x.message}${x.count > 1 ? ` (x${x.count})` : ""} at ${x.where}${trace}`));
      }
      for (const x of r.notes || []) body.append($("div", "note", pre + "note: " + x));
      if (r.output && r.output.length) body.append($("div", "out", r.output.map(l => pre + "game: " + l).join("\n")));
      const res = r.result;
      if (res && res.value !== undefined && e.cmd === "eval") body.append($("div", "out", pre + "= " + JSON.stringify(res.value)));
      if (res && res.aimed) for (const a of res.aimed)
        body.append($("div", "out", `${pre}clicked ${a.path} at ${a.at}` + (a.took ? `, which passed it to ${a.took.path}` : "")));
      if (res && res.clicked) for (const c of res.clicked)
        body.append($("div", "out", `${pre}click at ${c.at} went to ${c.took ? c.took.path : "no control"}`));
      if (r.shots && r.shots.length) {
        const s = $("div", "shots");
        for (const p of r.shots) { const a = $("a", "", p.split("/").pop()); a.href = p; a.target = "_blank"; s.append(a); }
        body.append(s);
      }
      if (res) {
        const d = $("details"); d.append($("summary", "", "reply"), $("pre", "", JSON.stringify(res, null, 2)));
        body.append(d);
      }
    }
    row.append(num, thumbs, body);
    list.append(row);
  }
  if (!list.children.length) list.append($("div", "empty", only ? "No commands with errors or notes." : "No commands yet."));
  const start = ENTRIES.find(e => e.cmd === "start");
  if (start) document.getElementById("title").textContent = `gdh live timeline: ${start.session}`;
  document.getElementById("sum").textContent =
    `${n} commands · ${failed} failed · ${errors} engine errors · last frame ${frame}`;
}

document.getElementById("only").addEventListener("change", render);
const zoom = document.getElementById("zoom");
document.getElementById("list").addEventListener("click", ev => {
  if (ev.target.tagName === "IMG") { zoom.firstElementChild.src = ev.target.src; zoom.style.display = "flex"; }
});
zoom.addEventListener("click", () => { zoom.style.display = "none"; });
render();
</script>
</body>
</html>
"""
