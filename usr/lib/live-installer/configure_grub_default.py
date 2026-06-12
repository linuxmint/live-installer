#!/usr/bin/python3
# coding: utf-8
"""Edit /etc/default/grub: kernel cmdline tokens and GRUB_* keys.

Standalone (stdlib only) so it can be copied into the target and run
with the target's python during an unattended install. All operations
are idempotent — running it twice produces the same file.

  --append-cmdline TOKEN   add TOKEN to GRUB_CMDLINE_LINUX_DEFAULT if absent
  --remove-cmdline TOKEN   remove an exact TOKEN from GRUB_CMDLINE_LINUX_DEFAULT
  --set KEY=VALUE          set/replace (or uncomment) a GRUB_* key, value quoted

Run update-grub afterwards to regenerate grub.cfg.
"""

import argparse
import re
import sys

CMDLINE_KEY = "GRUB_CMDLINE_LINUX_DEFAULT"


def edit_cmdline(text, appends, removes):
    pattern = re.compile(r'^%s="(.*)"' % CMDLINE_KEY, re.M)
    match = pattern.search(text)
    current = match.group(1) if match else ""
    tokens = current.split()
    tokens = [t for t in tokens if t not in removes]
    for token in appends:
        if token not in tokens:
            tokens.append(token)
    new_line = '%s="%s"' % (CMDLINE_KEY, " ".join(tokens))
    if match:
        return text[: match.start()] + new_line + text[match.end():]
    return text.rstrip("\n") + "\n" + new_line + "\n"


def set_key(text, key, value):
    new_line = '%s="%s"' % (key, value)
    # match the key whether active or commented out
    pattern = re.compile(r'^#?\s*%s=.*$' % re.escape(key), re.M)
    if pattern.search(text):
        return pattern.sub(new_line, text, count=1)
    return text.rstrip("\n") + "\n" + new_line + "\n"


def apply(text, appends, removes, sets):
    if appends or removes:
        text = edit_cmdline(text, appends, removes)
    for key, value in sets:
        text = set_key(text, key, value)
    return text


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", default="/etc/default/grub")
    parser.add_argument("--append-cmdline", action="append", default=[])
    parser.add_argument("--remove-cmdline", action="append", default=[])
    parser.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    args = parser.parse_args(argv)

    sets = []
    for item in args.set:
        if "=" not in item:
            parser.error("--set expects KEY=VALUE, got %r" % item)
        key, value = item.split("=", 1)
        sets.append((key, value))

    with open(args.file, encoding="utf-8") as f:
        text = f.read()
    text = apply(text, args.append_cmdline, set(args.remove_cmdline), sets)
    with open(args.file, "w", encoding="utf-8") as f:
        f.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
