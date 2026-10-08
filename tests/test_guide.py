"""gdh guide, and every gdh example in the docs against the CLI: each --flag must be one the command takes."""
import re
import subprocess

from conftest import ROOT

from gdh.cli import build_parser


def command_options(parser):
    """{('live', 'step'): {'--until', ...}, ...} for every command and subcommand of the parser."""
    found = {}

    def walk(p, path):
        found[path] = {s for a in p._actions for s in a.option_strings}
        for a in p._actions:
            if a.__class__.__name__ == "_SubParsersAction":
                for name, child in a.choices.items():
                    walk(child, (*path, name))
    walk(parser, ())
    return found


def doc_examples():
    """(file, example) for each `gdh ...` code span and each code line starting with gdh."""
    files = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md")), *sorted((ROOT / "skills").glob("*/SKILL.md")),
             ROOT / "agents" / "playtester.md"]
    for f in files:
        text = f.read_text()
        for example in re.findall(r"`(gdh [^`]+)`", text):
            yield f, example
        for line in text.splitlines():
            if line.strip().startswith("gdh "):
                yield f, line.strip()


def test_guide_prints_the_cheat_sheet():
    out = subprocess.run(["uv", "run", "gdh", "guide"], cwd=ROOT, capture_output=True, text=True, check=True).stdout
    assert "gdh live step --until" in out and len(out.splitlines()) < 70
    assert "gdh guide" in subprocess.run(["uv", "run", "gdh", "--help"], cwd=ROOT, capture_output=True,
                                         text=True).stdout


def test_every_documented_flag_is_one_the_command_takes():
    options = command_options(build_parser())
    from gdh.guide import GUIDE
    examples = [*doc_examples(), *(("guide", line.strip()) for line in GUIDE.splitlines()
                                   if line.strip().startswith("gdh "))]
    wrong = []
    for f, example in examples:
        example = example.split(" # ")[0].split(" -- ")[0]
        if f == "guide":
            example = example.split("  ")[0]
        path = ()
        for word in example.split()[1:]:
            if (*path, word) in options:
                path = (*path, word)
            elif not word.startswith("-"):
                break
        if not path:
            continue
        for flag in re.findall(r"(?<![\w-])(--[a-z][a-z0-9-]*)", example):
            if flag not in options[path]:
                wrong.append(f"{getattr(f, 'name', f)}: gdh {' '.join(path)} takes no {flag}: {example}")
    assert not wrong, "\n".join(wrong)
