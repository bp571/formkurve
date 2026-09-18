# Die Kreisliga A Hunsrück-Mosel Formkurve

Wer spielt gerade gut? Die offizielle Tabelle zählt die ganze Saison; diese Seite zeigt die Form
der letzten fünf Spieltage. Live unter [bp571.github.io/formkurve](https://bp571.github.io/formkurve/).

## Was die Seite zeigt

- **Formtabelle** — die 14 Teams sortiert nach Form der letzten fünf Spieltage, gegner- und
  ergebnisgewichtet. Daneben: Veränderung zum Vorspieltag, Saisonwert (0–100), Bilanz,
  Tore und der offizielle Tabellenplatz mit dem Abstand zum Formrang.
- **Aufstellung** — die Liga als Spielfeld, nach Form angeordnet.
- **Spieltag** — jedes Spiel mit seinen Torminuten, Anstoß, Platz, Zuschauern und der Prognose
  davor.
- **Marker** — *Mannschaft der Stunde*, *Formsprung*, *Comeback-Team*, *Führung verspielt*,
  *Überraschung* und *Topspiel*, jeweils nach einer festen Regel vergeben.
- **Team im Detail** — Ergebnisse, Restprogramm, Saisonverlauf, Torschützen, Belag und
  Angstgegner je Team.
- **Prognose** — Heim/Remis/Auswärts-Wahrscheinlichkeiten und erwartete Tore für den nächsten
  Spieltag, samt Bilanz gegen die Liga-Quote.
- **Statistik** — Torschützen, Belag und Heimbonus, Rückstand und Führung.
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
