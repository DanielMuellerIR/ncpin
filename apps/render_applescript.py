#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rendert die ncpin-AppleScript-Vorlage ohne fragile sed-Pfadquotierung."""

import sys


def applescript_string(value):
    """Quotet einen Wert als AppleScript-Stringliteral."""
    if "\n" in value or "\r" in value:
        raise ValueError("Zeilenumbrueche sind in Installationspfaden nicht erlaubt")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render(template, action, label, ncpin_path):
    if action not in ("local", "online"):
        raise ValueError("ungueltige Aktion: %s" % action)
    return (template
            .replace("@@ACTION@@", action)
            .replace("@@LABEL@@", label)
            .replace("@@NCPIN_PATH@@", applescript_string(ncpin_path)))


def main(argv):
    if len(argv) != 5:
        sys.stderr.write("Aufruf: render_applescript.py <vorlage> <action> <label> <ncpin> <ausgabe>\n")
        return 2
    template_path, action, label, ncpin_path, output_path = argv
    with open(template_path, "r", encoding="utf-8") as handle:
        template = handle.read()
    rendered = render(template, action, label, ncpin_path)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
