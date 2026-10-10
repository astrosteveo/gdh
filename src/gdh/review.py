"""gdh review: a page with options side by side, for the user to choose between, and their choice read back.

Each option is an image or a video with a label and the settings that made it. The page goes into a directory of its
own with copies of the files, so it opens anywhere. With --wait, gdh serves it on 127.0.0.1 and waits: the page saves
the choice and the notes into choice.json there, and gdh prints them. Without it the page offers the choice as a file
to download. `gdh review --read DIR` prints a choice saved earlier.
"""
import html
import json
import secrets
import shutil
import sys
import threading
import time
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from gdh.godot import GdhError

IMAGES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
VIDEOS = {".mp4", ".webm", ".ogv", ".mov"}
CHOICE = "choice.json"


class ReviewError(GdhError):
    pass


def options_from(args):
    """The options: [{"label", "file", "settings"}] from LABEL=PATH (or PATH) arguments, --settings LABEL=TEXT, and
    --from a sweep's or a matrix's JSON."""
    options = []
    for item in args.from_ or []:
        options += options_of_record(Path(item))
    for item in args.options:
        label, sep, path = item.partition("=")
        if not sep or not Path(path).exists() and Path(item).exists():
            label, path = Path(item).stem, item
        options.append({"label": label, "file": path, "settings": ""})
    settings = {}
    for item in args.settings:
        label, sep, text = item.partition("=")
        if not sep:
            raise ReviewError(f"--settings takes LABEL=TEXT, not {item!r}.")
        settings[label] = text
    labels = [o["label"] for o in options]
    for label in settings:
        if label not in labels:
            raise ReviewError(f"--settings names {label!r}, which isn't an option's label: {', '.join(labels)}.")
    for o in options:
        o["settings"] = settings.get(o["label"], o["settings"])
    if len(options) < 2:
        raise ReviewError("A review needs 2 options or more: LABEL=PATH, PATH, or --from a sweep's or a matrix's JSON.")
    if len(set(labels)) != len(labels):
        raise ReviewError(f"Each option needs a label of its own: {', '.join(labels)}.")
    for o in options:
        path = Path(o["file"])
        if not path.is_file():
            raise ReviewError(f"No such file: {path} (option {o['label']}).")
        if path.suffix.lower() not in IMAGES | VIDEOS:
            raise ReviewError(f"{path} isn't an image or a video ({', '.join(sorted(IMAGES | VIDEOS))}).")
    return options


def options_of_record(path):
    """Options from a sweep's cells (sweep.json) or a matrix's (matrix.json)."""
    try:
        record = json.loads(path.read_text())
    except (OSError, ValueError) as e:
        raise ReviewError(f"Can't read {path}: {e}") from None
    cells = record.get("cells") if isinstance(record, dict) else None
    if not cells:
        raise ReviewError(f"{path} isn't a sweep's or a matrix's record: it has no cells.")
    out = []
    for cell in cells:
        if "image" in cell:  # a sweep
            out.append({"label": cell["label"], "file": cell["image"], "settings": cell["label"]})
        else:  # a matrix
            settings = "\n".join(f"{'/'.join(k.split('/')[-2:])} = {str(v).lower() if isinstance(v, bool) else v}"
                                 for k, v in cell.get("settings", {}).items())
            out.append({"label": f"{cell['screen']} {cell['quality']}", "file": str(Path(cell["dir"]) / "normal.png"),
                        "settings": f"{cell['size']}" + (f"\n{settings}" if settings else "")})
    return out


def write_page(options, out, title, question, token):
    """Copy the files into out/media/ and write out/index.html. Returns the page's path."""
    media = out / "media"
    if media.exists():
        shutil.rmtree(media)
    media.mkdir(parents=True)
    (out / CHOICE).unlink(missing_ok=True)
    cards = []
    for i, o in enumerate(options, 1):
        src = Path(o["file"])
        name = f"{i:02d}{src.suffix.lower()}"
        shutil.copyfile(src, media / name)
        if src.suffix.lower() in VIDEOS:
            view = f'<video src="media/{name}" controls loop muted playsinline></video>'
        else:
            view = f'<a href="media/{name}" target="_blank"><img src="media/{name}" alt="{html.escape(o["label"])}"></a>'
        text = o["settings"]
        settings = ""
        if text and text.count("\n") >= 3:  # long: folded under its first line
            first, rest = text.split("\n", 1)
            settings = (f'<details><summary>{html.escape(first)}</summary><pre>{html.escape(rest)}</pre></details>')
        elif text:
            settings = f'<pre>{html.escape(text)}</pre>'
        cards.append(f'''<label class="card" data-label="{html.escape(o["label"], quote=True)}">
  <div class="view">{view}</div>
  <div class="caption"><input type="radio" name="choice" value="{html.escape(o["label"], quote=True)}">
    <span class="key">{i}</span> <strong>{html.escape(o["label"])}</strong></div>
  {settings}
</label>''')
    page = PAGE.replace("{{title}}", html.escape(title)).replace("{{question}}", html.escape(question)) \
        .replace("{{cards}}", "\n".join(cards)).replace("{{token}}", token) \
        .replace("{{options}}", json.dumps([{"label": o["label"], "settings": o["settings"], "file": o["file"]}
                                            for o in options]).replace("</", "<\\/"))
    path = out / "index.html"
    path.write_text(page)
    return path


class Handler(SimpleHTTPRequestHandler):
    """Serves the review's directory, and takes the choice as a POST to /choice with the page's token."""
    token = ""
    received = None

    def do_POST(self):
        if self.path != "/choice":
            self.send_error(404)
            return
        try:
            data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        except ValueError:
            self.send_error(400, "Not JSON")
            return
        if data.get("token") != self.token:
            self.send_error(403, "Wrong token")
            return
        data.pop("token")
        data["saved_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        (Path(self.directory) / CHOICE).write_text(json.dumps(data, indent=1) + "\n")
        Handler.received = data
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"saved": true}')

    def log_message(self, *args):
        pass


def describe(choice):
    lines = [f"chosen: {choice.get('choice') or '(none)'}"]
    if choice.get("notes"):
        lines.append(f"notes: {choice['notes']}")
    return "\n".join(lines)


def cmd_review(args):
    if args.read:
        path = Path(args.read) / CHOICE if Path(args.read).is_dir() else Path(args.read)
        if not path.is_file():
            raise ReviewError(f"No choice saved yet at {path}.")
        choice = json.loads(path.read_text())
        print(json.dumps(choice, indent=1) if args.json else describe(choice))
        return 0
    if not args.out:
        raise ReviewError("review needs --out DIR: where the page goes.")
    options = options_from(args)
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(12)
    page = write_page(options, out, args.title, args.question, token if args.wait else "")
    if not args.wait:
        print(f"review: {page} ({len(options)} options). Open it, choose, and save the choice into {out / CHOICE}; "
              f"gdh review --read {out} prints it")
        return 0
    Handler.token = token
    server = ThreadingHTTPServer(("127.0.0.1", args.port), lambda *a, **k: Handler(*a, directory=str(out), **k))
    url = f"http://127.0.0.1:{server.server_address[1]}/index.html"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"review: {url} ({len(options)} options), waiting up to {args.timeout} s for the choice", flush=True)
    if args.open:
        webbrowser.open(url)
    deadline = time.monotonic() + args.timeout
    try:
        while Handler.received is None and time.monotonic() < deadline:
            time.sleep(0.2)
    finally:
        server.shutdown()
    if Handler.received is None:
        print(f"gdh: no choice within {args.timeout} s; the page is at {page}", file=sys.stderr)
        return 1
    print(json.dumps(Handler.received, indent=1) if args.json else describe(Handler.received))
    return 0


def add_parser(sub):
    p = sub.add_parser("review", help="A page with options side by side (images or videos, each with its settings) "
                                      "for the user to choose between, and their choice read back")
    p.add_argument("options", nargs="*", metavar="LABEL=PATH",
                   help="An option: an image or a video, with a label (default its file's name)")
    p.add_argument("--from", dest="from_", action="append", metavar="FILE.json",
                   help="Options from a sweep's cells (gdh live sweep writes FILE.json beside its grid) or a matrix's "
                        "(matrix.json); repeatable")
    p.add_argument("--settings", action="append", default=[], metavar="LABEL=TEXT",
                   help="What made an option, shown under it; repeatable")
    p.add_argument("--out", metavar="DIR", help="Where the page and copies of the files go")
    p.add_argument("--title", default="Which one?", help="The page's title")
    p.add_argument("--question", default="Pick the one that looks best, and say why if you like.",
                   help="What the page asks")
    p.add_argument("--wait", action="store_true",
                   help="Serve the page on 127.0.0.1 and wait for the choice, which it saves into DIR/choice.json, "
                        "then print it")
    p.add_argument("--open", action="store_true", help="With --wait: open the page in the browser")
    p.add_argument("--port", type=int, default=0, help="With --wait: the port (default a free one)")
    p.add_argument("--timeout", type=int, default=1800, metavar="S", help="With --wait: seconds to wait (default 1800)")
    p.add_argument("--read", metavar="DIR", help="Print the choice saved in DIR (or a choice.json), and do nothing else")
    p.add_argument("--json", action="store_true", help="Print the choice as JSON")
    p.set_defaults(func=cmd_review)


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}}</title>
<style>
:root { --bg: #f6f6f4; --card: #ffffff; --ink: #1d1d1b; --muted: #6b6b66; --line: #dcdcd6; --pick: #2563c9; }
@media (prefers-color-scheme: dark) {
  :root { --bg: #161616; --card: #202020; --ink: #ececea; --muted: #a0a09a; --line: #353533; --pick: #6aa1ff; }
}
* { box-sizing: border-box; }
body { margin: 0; padding: 20px 16px 120px; background: var(--bg); color: var(--ink);
       font: 15px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
h1 { font-size: 20px; margin: 0 0 4px; }
.question { color: var(--muted); margin: 0 0 16px; }
.cards { display: grid; gap: 14px; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
.card { display: block; background: var(--card); border: 2px solid var(--line); border-radius: 10px; padding: 10px;
        cursor: pointer; }
.card.chosen { border-color: var(--pick); box-shadow: 0 0 0 2px var(--pick); }
.view img, .view video { width: 100%; height: auto; display: block; border-radius: 6px; background: #000; }
.caption { display: flex; align-items: center; gap: 8px; margin-top: 8px; }
.key { display: inline-block; min-width: 1.5em; text-align: center; border: 1px solid var(--line); border-radius: 4px;
       color: var(--muted); font-size: 12px; }
details { margin-top: 6px; color: var(--muted); font-size: 12px; }
pre { margin: 6px 0 0; color: var(--muted); font-size: 12px; white-space: pre-wrap; word-break: break-word; }
.bar { position: fixed; left: 0; right: 0; bottom: 0; background: var(--card); border-top: 1px solid var(--line);
       padding: 10px 16px; display: flex; gap: 10px; align-items: flex-start; flex-wrap: wrap; }
textarea { flex: 1 1 300px; min-height: 2.6em; background: var(--bg); color: var(--ink); border: 1px solid var(--line);
           border-radius: 6px; padding: 6px 8px; font: inherit; }
button { background: var(--pick); color: #fff; border: 0; border-radius: 6px; padding: 9px 16px; font: inherit;
         cursor: pointer; }
#status { color: var(--muted); align-self: center; }
</style>
</head>
<body>
<h1>{{title}}</h1>
<p class="question">{{question}} Press 1&ndash;9 to pick.</p>
<div class="cards">
{{cards}}
</div>
<div class="bar">
  <textarea id="notes" placeholder="Notes (optional): why this one, what to change"></textarea>
  <button id="save" type="button">Save choice</button>
  <span id="status"></span>
</div>
<script type="application/json" id="options">{{options}}</script>
<script>
const token = "{{token}}";
const options = JSON.parse(document.getElementById("options").textContent);
const cards = [...document.querySelectorAll(".card")];
function pick(card) {
  cards.forEach(c => c.classList.toggle("chosen", c === card));
  card.querySelector("input").checked = true;
}
cards.forEach(c => c.addEventListener("click", () => pick(c)));
document.addEventListener("keydown", e => {
  if (e.target.tagName === "TEXTAREA") return;
  const n = parseInt(e.key, 10);
  if (n >= 1 && n <= cards.length) pick(cards[n - 1]);
});
document.getElementById("save").addEventListener("click", async () => {
  const chosen = document.querySelector("input[name=choice]:checked");
  const status = document.getElementById("status");
  if (!chosen) { status.textContent = "Pick one first."; return; }
  const choice = { choice: chosen.value, notes: document.getElementById("notes").value.trim(),
                   options: options.map(o => o.label),
                   settings: (options.find(o => o.label === chosen.value) || {}).settings || "" };
  if (token) {
    try {
      const r = await fetch("/choice", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ ...choice, token }) });
      if (r.ok) { status.textContent = "Saved. You can close this page."; return; }
      status.textContent = "Couldn't save (" + r.status + "): downloading it instead.";
    } catch (err) { status.textContent = "gdh isn't waiting any more: downloading it instead."; }
  }
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([JSON.stringify(choice, null, 1)], { type: "application/json" }));
  a.download = "choice.json";
  a.click();
  if (!token) status.textContent = "Downloaded choice.json: put it in this page's folder for gdh review --read.";
});
</script>
</body>
</html>
"""
