#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Rendert die ncpin-AppleScript-Vorlage ohne fragile sed-Pfadquotierung.

Der Pfad zur CLI wird bewusst NICHT mehr eingesetzt: Die App findet ihre
eingebettete Kopie zur Laufzeit im eigenen Bundle. So steht in einem
ausgelieferten Droplet kein absoluter Pfad des Build-Macs.
"""

import sys


def render(template, action, label):
    if action not in ("local", "online"):
        raise ValueError("ungueltige Aktion: %s" % action)
    if "\n" in label or "\r" in label:
        raise ValueError("Zeilenumbrueche sind im Label nicht erlaubt")
    if '"' in label or "\\" in label:
        raise ValueError('Anfuehrungszeichen und Backslash sind im Label nicht erlaubt')
    return (template
            .replace("@@ACTION@@", action)
            .replace("@@LABEL@@", label))


def main(argv):
    if len(argv) != 4:
        sys.stderr.write("Aufruf: render_applescript.py <vorlage> <action> <label> <ausgabe>\n")
        return 2
    template_path, action, label, output_path = argv
    with open(template_path, "r", encoding="utf-8") as handle:
        template = handle.read()
    rendered = render(template, action, label)
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
