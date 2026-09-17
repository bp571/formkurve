# Power Ranking — Kreisliga A Hunsrück-Mosel

Wer spielt gerade gut? Die offizielle Tabelle zählt die ganze Saison; diese Seite zeigt die Form
der letzten fünf Spieltage. Live unter [bp571.github.io/power-ranking](https://bp571.github.io/power-ranking/).

## Was die Seite zeigt

- **Formtabelle** — die 14 Teams sortiert nach Form der letzten fünf Spieltage, gegner- und
  ergebnisgewichtet. Daneben: Veränderung zum Vorspieltag, Saison-Powerscore (0–100), Bilanz,
  Tore und der offizielle Tabellenplatz mit dem Abstand zum Formrang.
- **Formfeld** — die Liga als Spielfeld, nach Form angeordnet.
- **Marker** — *Mannschaft der Stunde*, *Formsprung*, *Comeback-Team*, *Führung verspielt*,
  *Überraschung des Spieltags* und *Topspiel*, jeweils nach einer festen Regel vergeben.
- **Belag** — Tore pro Spiel und Heimbonus auf Rasen und Kunstrasen.
- **Rückstand und Führung** — wer Rückstände dreht und wer Führungen hergibt.
- **Prognose** — Heim/Remis/Auswärts-Wahrscheinlichkeiten und erwartete Tore für den nächsten
  Spieltag, samt Bilanz, wie gut die Prognose bisher war (Spoiler: kaum besser als die Tabelle).
- **Erklärungen** — ein eigener Tab, der jede Spalte und jeden Marker erklärt.

## Spieltage und Archiv

- Jeder Spieltag der laufenden Saison hat eine eigene Momentaufnahme
  (`/2026-27/spieltag-NN/`) — der Stand direkt nach diesem Spieltag, erreichbar über die
  Spieltagsleiste im Kopf der Seite.
- Abgeschlossene Saisons liegen als Archivseite mit Formverlauf-Chart vor (`/2025-26/`).
- Ein Saisonwechsler im Kopf verbindet alle Seiten.

## Datenquelle

Ergebnisse von [fussball.de](https://www.fussball.de). Die Seite veröffentlicht nur abgeleitete
Werte, keine Rohdaten und keine Spielernamen.

## Aktualisieren

```
python run.py        # Ergebnisse holen, bewerten, alle Seiten schreiben
```

Danach committen und pushen — GitHub Pages baut in etwa einer Minute neu. Technische Details,
Modell und Messungen stehen in [CLAUDE.md](CLAUDE.md).
