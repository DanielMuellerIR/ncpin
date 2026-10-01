#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Baut eine macOS Quick Action (Finder-Rechtsklick "Schnellaktionen") als
.workflow-Bundle, die ncpin auf die ausgewählten Dateien anwendet.

Eine Quick Action ist im Kern ein Automator-Dokument mit einer einzigen
"Shell-Skript ausführen"-Aktion. macOS zeigt sie im Finder-Kontextmenü, wenn
das Bundle in ~/Library/Services/ liegt und der Service registriert ist.

Aufruf:
  make_quickaction.py <action> <menü-titel> <ncpin-pfad> <ausgabe-bundle>
    action      "local" oder "online"
    menü-titel  Text im Rechtsklick-Menü (z.B. "Lokal halten (Nextcloud)")
    ncpin-pfad  absoluter Pfad zur ncpin-CLI. install.sh übergibt hier die
                Kopie in der INSTALLIERTEN App, nicht die im Repo: Ein
                .workflow-Bundle kann seinen eigenen Ort zur Laufzeit nicht
                ermitteln, braucht also einen absoluten Pfad — und der ins Repo
                wäre der des Build-Macs und damit nach einem Umzug tot.
    ausgabe     Zielpfad des .workflow-Bundles
"""

import os
import plistlib
import subprocess
import sys


def uuid():
    """Zufällige UUID via uuidgen (in Workflow-Skripten kein random verfügbar,
    hier als normales CLI-Tool aber problemlos)."""
    return subprocess.check_output(["/usr/bin/uuidgen"]).decode().strip()


def build(action, title, ncpin, out_bundle):
    # Shell-Skript der Aktion: ncpin auf alle übergebenen Pfade ("$@"),
    # danach kurze Notification als Rückmeldung.
    if action == "local":
        note = "Download angefordert."
    else:
        note = "Speicherfreigabe angefordert."
    # Der Pfad zeigt in die installierte App (siehe install.sh). Fehlt sie, wuerde
    # zsh nur "command not found" liefern und die Meldung unten "Fehler (Code
    # 127)" anzeigen — daran erkennt niemand die Ursache. Deshalb vorher
    # nachsehen und im Klartext sagen, was fehlt.
    command = (
        '#!/bin/zsh\n'
        '# Eingabe kommt als Argumente (inputMethod=1).\n'
        'ncpin=%s\n'
        'if [ ! -x "$ncpin" ]; then\n'
        '  /usr/bin/osascript -e \'display notification "Die zugehörige App fehlt — bitte ncpin neu installieren." with title "ncpin"\'\n'
        '  exit 1\n'
        'fi\n'
        '"$ncpin" %s "$@"\n'
        'rc=$?\n'
        'if [ $rc -eq 0 ]; then\n'
        '  /usr/bin/osascript -e \'display notification "%s" with title "Nextcloud"\'\n'
        'else\n'
        '  /usr/bin/osascript -e \'display notification "Fehler (Code \'"$rc"\')" with title "ncpin"\'\n'
        'fi\n'
    ) % (_q(ncpin), action, note)

    in_uuid, out_uuid, act_uuid = uuid(), uuid(), uuid()

    # --- document.wflow: das Automator-Dokument -----------------------------
    action_dict = {
        "action": {
            "AMAccepts": {
                "Container": "List",
                "Optional": False,
                "Types": ["com.apple.cocoa.path"],
            },
            "AMActionVersion": "2.0.3",
            "AMApplication": ["Automator"],
            "AMParameterProperties": {
                "COMMAND_STRING": {},
                "CheckedForUserDefaultShell": {},
                "inputMethod": {},
                "shell": {},
                "source": {},
            },
            "AMProvides": {
                "Container": "List",
                "Types": ["com.apple.cocoa.path"],
            },
            "ActionBundlePath": "/System/Library/Automator/Run Shell Script.action",
            "ActionName": "Run Shell Script",
            "ActionParameters": {
                "COMMAND_STRING": command,
                "CheckedForUserDefaultShell": True,
                "inputMethod": 1,           # 1 = Eingabe als Argumente ($@)
                "shell": "/bin/zsh",
                "source": "",
            },
            "BundleIdentifier": "com.apple.RunShellScript",
            "CFBundleVersion": "2.0.3",
            "CanShowSelectedItemsWhenRun": False,
            "CanShowWhenRun": True,
            "Category": ["AMCategoryUtilities"],
            "Class Name": "RunShellScriptAction",
            "InputUUID": in_uuid,
            "Keywords": ["Shell", "Script", "Command", "Run", "Unix"],
            "OutputUUID": out_uuid,
            "UUID": act_uuid,
            "UnlocalizedApplications": ["Automator"],
            "arguments": {
                "0": {
                    "default value": 0,
                    "name": "inputMethod",
                    "required": "0",
                    "type": "0",
                    "uuid": "0",
                },
                "1": {
                    "default value": "",
                    "name": "source",
                    "required": "0",
                    "type": "0",
                    "uuid": "1",
                },
                "2": {
                    "default value": False,
                    "name": "CheckedForUserDefaultShell",
                    "required": "0",
                    "type": "0",
                    "uuid": "2",
                },
                "3": {
                    "default value": "",
                    "name": "COMMAND_STRING",
                    "required": "0",
                    "type": "0",
                    "uuid": "3",
                },
                "4": {
                    "default value": "/bin/sh",
                    "name": "shell",
                    "required": "0",
                    "type": "0",
                    "uuid": "4",
                },
            },
            "isViewVisible": 1,
            "location": "309.000000:253.000000",
            "nibPath": "/System/Library/Automator/Run Shell Script.action/Contents/Resources/main.nib",
        },
        "isViewVisible": 1,
    }

    wflow = {
        "AMApplicationBuild": "528",
        "AMApplicationVersion": "2.10",
        "AMDocumentVersion": "2",
        "actions": [action_dict],
        "connectors": {},
        "workflowMetaData": {
            "applicationBundleIDsByPath": {},
            "applicationPaths": [],
            "inputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "outputTypeIdentifier": "com.apple.Automator.nothing",
            # presentationMode ist eine Bitmaske, WO die Aktion erscheint.
            # 15 (=8+4+2+1) inkl. Bit "4" = Finder-Schnellaktionen mit eigenem
            # Namen. 11 (=8+2+1, ohne "4") landet im generischen
            # "Shell-Skript ausfuehren" — genau das wollen wir nicht.
            "presentationMode": 15,
            "processesInput": False,
            "serviceApplicationBundleID": "com.apple.finder",
            "serviceApplicationPath": "/System/Library/CoreServices/Finder.app",
            "serviceInputTypeIdentifier": "com.apple.Automator.fileSystemObject",
            "serviceOutputTypeIdentifier": "com.apple.Automator.nothing",
            "serviceProcessesInput": 0,
            "systemImageName": "NSActionTemplate",
            "useAutomaticInputType": 0,
            "workflowTypeIdentifier": "com.apple.Automator.servicesMenu",
        },
    }

    # --- Info.plist: meldet den Service im System an ------------------------
    info = {
        "NCPINOwnerIdentifier": "com.ethermac.ncpin.quickaction.%s" % action,
        "NSServices": [
            {
                # Icon + Hintergrundfarbe wie bei funktionierenden Quick Actions
                # (z.B. PeaZip) — sonst zeigt der Finder die Aktion nicht sauber.
                "NSBackgroundColorName": "background",
                "NSIconName": "NSTouchBarHome",
                "NSMenuItem": {"default": title},
                "NSMessage": "runWorkflowAsService",
                "NSRequiredContext": {"NSApplicationIdentifier": "com.apple.finder"},
                "NSSendFileTypes": ["public.item"],
            }
        ],
    }

    # --- Bundle schreiben ---------------------------------------------------
    contents = os.path.join(out_bundle, "Contents")
    os.makedirs(contents, exist_ok=True)
    with open(os.path.join(contents, "document.wflow"), "wb") as f:
        plistlib.dump(wflow, f)
    with open(os.path.join(contents, "Info.plist"), "wb") as f:
        plistlib.dump(info, f)
    print(out_bundle)


def _q(s):
    """Pfad fürs Shell-Skript quoten (einfache Anführungszeichen sicher)."""
    return "'" + s.replace("'", "'\\''") + "'"


if __name__ == "__main__":
    if len(sys.argv) != 5:
        sys.stderr.write(__doc__)
        sys.exit(2)
    build(sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4])
