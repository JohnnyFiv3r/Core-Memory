"""Sets which models read and judge in a copy of core-memory.toml, for a run
of the smoke test or of the hard cases, so readers and judges can be
compared on the same messages.

    configure.py <config> [--reader-only] [--reader-effort E]
                          [--judge-model M] [--judge-effort E]

--reader-only takes the [judge] section out, so nothing is judged.
--reader-effort and --judge-effort set a binding's reasoning_effort
(`none` turns thinking off, which only some models allow).
--judge-model sets the judge binding's model.

The file is edited in place and read back, and every change is checked.
"""

import re
import sys
import tomllib

EFFORTS = {"none", "low", "medium", "high", "xhigh", "max"}
# A pinned Anthropic model, as the bindings name one.
MODEL = re.compile(r"claude-[a-z0-9][a-z0-9.-]*")


def blocks(text):
    """The file as (header, lines) blocks, each from a table header to the
    next. The first block, before any header, has the header ''."""
    out = [["", []]]
    for line in text.splitlines(keepends=True):
        if line.startswith("["):
            out.append([line.strip(), []])
        out[-1][1].append(line)
    return out


def binding_block(parts, binding_id):
    for header, lines in parts:
        if header == "[[binding]]" and any(
            re.fullmatch(rf'id\s*=\s*"{re.escape(binding_id)}"\s*', l.strip()) for l in lines
        ):
            return lines
    sys.exit(f"configure.py: the config has no binding `{binding_id}`")


def set_key(lines, key, value):
    for i, line in enumerate(lines):
        if re.match(rf"{key}\s*=", line):
            lines[i] = f'{key} = "{value}"\n'
            return
    # Not there: added after the binding's id.
    at = next(i for i, l in enumerate(lines) if l.startswith("id"))
    lines.insert(at + 1, f'{key} = "{value}"\n')


def main(argv):
    if not argv:
        sys.exit(__doc__)
    path, rest = argv[0], argv[1:]
    options = {"--reader-only": False}
    i = 0
    while i < len(rest):
        flag = rest[i]
        if flag == "--reader-only":
            options[flag] = True
            i += 1
        elif flag in ("--reader-effort", "--judge-model", "--judge-effort") and i + 1 < len(rest):
            options[flag] = rest[i + 1]
            i += 2
        else:
            sys.exit(f"configure.py: unknown option {flag!r}\n{__doc__}")
    for flag in ("--reader-effort", "--judge-effort"):
        if flag in options and options[flag] not in EFFORTS:
            sys.exit(f"configure.py: {flag} must be one of {', '.join(sorted(EFFORTS))}")
    model = options.get("--judge-model")
    if model is not None and not MODEL.fullmatch(model):
        sys.exit("configure.py: --judge-model must be a model id such as claude-sonnet-5-5")
    if options["--reader-only"] and ("--judge-model" in options or "--judge-effort" in options):
        sys.exit("configure.py: a reader-only run has no judge to set")

    with open(path, encoding="utf-8") as f:
        parts = blocks(f.read())
    judged = any(header == "[judge]" for header, _ in parts)
    if not judged and ("--judge-model" in options or "--judge-effort" in options):
        sys.exit("configure.py: the config has no [judge] section, so there is no judge to set")
    if options["--reader-only"]:
        parts = [p for p in parts if p[0] != "[judge]"]
    if "--reader-effort" in options:
        set_key(binding_block(parts, "reader"), "reasoning_effort", options["--reader-effort"])
    if model is not None:
        set_key(binding_block(parts, "judge"), "model", model)
    if "--judge-effort" in options:
        set_key(binding_block(parts, "judge"), "reasoning_effort", options["--judge-effort"])
    text = "".join("".join(lines) for _, lines in parts)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)

    # Read back: what was asked is what the file now says.
    config = tomllib.loads(text)
    bindings = {b["id"]: b for b in config.get("binding", [])}
    if options["--reader-only"]:
        assert "judge" not in config, "the [judge] section is still there"
    if "--reader-effort" in options:
        assert bindings["reader"]["reasoning_effort"] == options["--reader-effort"]
    if model is not None:
        assert bindings["judge"]["model"] == model
    if "--judge-effort" in options:
        assert bindings["judge"]["reasoning_effort"] == options["--judge-effort"]


if __name__ == "__main__":
    main(sys.argv[1:])
