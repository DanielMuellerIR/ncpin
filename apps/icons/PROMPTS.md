# Icon-Prompts

## Aktuell im Einsatz (Stand 2026-06-07, v1.0.5) — Flat-Variante

`local.png` / `online.png` = **flache Vektor-Optik**, lokal generiert mit
**mflux Z-Image-Turbo**, **Seed 123**, 8 Steps, 1024×1024.

- `local.png` („Lokal halten") — Wolke nur als **blauer Umriss, innen leer** + deutlicher Pin.
- `online.png` („Speicher freigeben") — Wolke **blau gefüllt**, ohne Pin.

Prompts: `prompt_local.txt` / `prompt_online.txt` (liegen im lokalen
Bildgenerierungs-Arbeitsordner, nicht im Repo). Negative-Prompts halten online
frei von Pin/Zipfel, local frei von Füllung.

> **TODO (offen):** sehr ähnliche Icons nochmal generieren, aber mit
> **fotorealistischer Pin-Nadel** wie in den früheren Icons (Familie A1 unten).
> Frühere PNGs: nur noch in git-History (aus dem Tree entfernt, 2026-06-07);
> Prompts vollständig hier dokumentiert.

---

## Frühere Variante (3D, OpenAI-Bildmodell)

Quelle: Feld `revised_prompt` der `image_generation_call`-Einträge einer
früheren Generierungssession (2026-06-06). 12 Kandidaten
(3 Motiv-Familien × lokal/freigeben × 2 Varianten).

**Damals ausgewählt:** Familie **„cloud + pin, candidate A1"** — blaue Wolke über
kleiner Disk-Basis, gehalten von einer silbernen Pinnnadel (fotorealistisch).

Tool: OpenAI-Bildmodell über Codex (`image_generation`), 1024×1024 PNG.
ncpin baut aus dem PNG via `apps/png2icns.sh` das `.icns`.

---

## Ausgewählt — Original (blau)

### [1] Lokal halten (blau)

```
Use case: logo-brand
Asset type: macOS app icon candidate, square 1024x1024 PNG.
Project context: ncpin controls Nextcloud Virtual Files state. This icon is for the droplet action "Lokal halten": make a selected cloud file available locally, hydrated and pinned.
Motif family: cloud + pin, candidate A1.
Primary request: Create a polished macOS-style app icon with a rounded-square background. Show a clean blue cloud symbol centered above a small solid local disk/base, held by a strong silver pushpin. The visual should imply "kept on this Mac" and "pinned locally".
Style: modern Apple utility icon, crisp vector-like shapes, subtle depth, clean highlights, no busy detail, readable at small sizes.
Palette: Nextcloud-inspired blue and white, with silver/graphite accent and a small warm highlight.
Constraints: no text, no letters, no numbers, no watermark, no exact Nextcloud trademark logo, no Finder face, no people, no photorealism, no transparent background.
```

### [2] Speicher freigeben (blau)

```
Use case: logo-brand
Asset type: macOS app icon candidate, square 1024x1024 PNG.
Project context: ncpin controls Nextcloud Virtual Files state. This icon is for the droplet action "Speicher freigeben": make a selected local cloud file online-only, replacing it with a tiny placeholder and freeing disk space.
Motif family: cloud + pin, candidate A1 companion to a local-pin icon.
Primary request: Create a polished macOS-style app icon with a rounded-square background. Show a clean blue cloud hovering above a small local disk/base. A silver pushpin is lifted upward and away from the disk, and the disk/base has a subtle open empty slot or glow to imply released space. The cloud remains present but feels lighter, online-only, and no longer pinned.
Style: modern Apple utility icon, crisp vector-like shapes, subtle depth, clean highlights, no busy detail, readable at small sizes.
Palette: Nextcloud-inspired blue and white, with silver/graphite accent and a small fresh green or cyan highlight.
Constraints: no text, no letters, no numbers, no watermark, no exact Nextcloud trademark logo, no Finder face, no people, no photorealism, no transparent background.
```

---

## Neu gewünscht — Ampel-Farben (Wolke grün / rot)

Gleiche Komposition wie das ausgewählte A1-Paar, nur die **Wolkenfarbe** ändert
sich: lokal halten = grün, freigeben = rot. Tipp für konsistentes Paar: dem
Bildmodell `local.png` bzw. `online.png` als Referenzbild beilegen und nur die
Farbe umfärben lassen.

### [1g] Lokal halten — GRÜN

```
Use case: logo-brand
Asset type: macOS app icon candidate, square 1024x1024 PNG.
Project context: ncpin controls Nextcloud Virtual Files state. This icon is for the droplet action "Lokal halten": make a selected cloud file available locally, hydrated and pinned.
Motif family: cloud + pin, candidate A1 — GREEN keep-local variant.
Primary request: Create a polished macOS-style app icon with a rounded-square background. Show a clean GREEN cloud symbol centered above a small solid local disk/base, held by a strong silver pushpin. The visual should imply "kept on this Mac" and "pinned locally". Keep the overall composition, lighting and proportions identical to the existing blue version; only the cloud color changes to green.
Style: modern Apple utility icon, crisp vector-like shapes, subtle depth, clean highlights, no busy detail, readable at small sizes.
Palette: fresh emerald / grass green and white, with silver/graphite accent and a small warm highlight; keep the rounded-square background a soft neutral or very pale green so the green cloud stays the clear focus.
Constraints: no text, no letters, no numbers, no watermark, no exact Nextcloud trademark logo, no Finder face, no people, no photorealism, no transparent background.
```

### [2r] Speicher freigeben — ROT

```
Use case: logo-brand
Asset type: macOS app icon candidate, square 1024x1024 PNG.
Project context: ncpin controls Nextcloud Virtual Files state. This icon is for the droplet action "Speicher freigeben": make a selected local cloud file online-only, replacing it with a tiny placeholder and freeing disk space.
Motif family: cloud + pin, candidate A1 companion — RED free-up variant.
Primary request: Create a polished macOS-style app icon with a rounded-square background. Show a clean RED cloud hovering above a small local disk/base. A silver pushpin is lifted upward and away from the disk, and the disk/base has a subtle open empty slot or glow to imply released space. The cloud remains present but feels lighter, online-only, and no longer pinned. Keep the overall composition, lighting and proportions identical to the existing blue version; only the cloud color changes to red.
Style: modern Apple utility icon, crisp vector-like shapes, subtle depth, clean highlights, no busy detail, readable at small sizes.
Palette: clear signal red / coral and white, with silver/graphite accent; keep the rounded-square background a soft neutral so the red cloud reads as the clear focus.
Constraints: no text, no letters, no numbers, no watermark, no exact Nextcloud trademark logo, no Finder face, no people, no photorealism, no transparent background.
```

---

## Alle 12 Original-Kandidaten (Kurzfassung)

| # | Aktion | Familie | Variante | Kernidee |
|---|--------|---------|----------|----------|
| 1 | lokal | cloud+pin | A1 ✅ | blaue Wolke über Disk, silberne Pinnnadel hält fest |
| 2 | freigeben | cloud+pin | A1 ✅ | Wolke über Disk, Pinnnadel angehoben, freier Slot |
| 3 | lokal | cloud+pin | A2 | blauer Map-Pin durchsticht File-Tile in die Disk |
| 4 | freigeben | cloud+pin | A2 | Map-Pin herausgezogen, leerer Slot, Cyan-Glow |
| 5 | lokal | transfer+disk | B1 | dicker blauer Abwärtspfeil trägt File in die Disk |
| 6 | freigeben | transfer+disk | B1 | Cyan-Aufwärtspfeil trägt File aus Disk in Wolke |
| 7 | lokal | transfer+disk | B2 | File fällt über 3 Leuchtpunkte in Mac-Tray |
| 8 | freigeben | transfer+disk | B2 | File steigt über Cyan-Punkte aus leerem Tray |
| 9 | lokal | droplet | C1 | blauer Tropfen fällt aus Wolke in File-Tile |
| 10 | freigeben | droplet | C1 | Tropfen verdunstet aufwärts, Slot leer |
| 11 | lokal | droplet | C2 | großer wolkenförmiger Tropfen in Disk-Tray, File darin |
| 12 | freigeben | droplet | C2 | heller Outline-Tropfen steigt aus leerem Tray |
