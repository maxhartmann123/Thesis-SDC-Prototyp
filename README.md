# Thesis-SDC-Prototyp-

Dieses Repository beinhaltet die prototypische Umsetzung eines SDC Providers (Patient Monitor) und die Umsetzung eines Consumers, welcher dessen Daten empfängt. Als Grundlage wurde der öffentlich verfügbare Drägerwerk Repository SDC Python Stack verwendet.
Das Dräger Repository wurde als Grundlage genutzt und darauf die Testskripts sowie den Provider und Consumer weiterentwickelt und für meine Zwecke angepasst.

Referenz Repository
https://github.com/Draegerwerk/sdc11073

Diese Software-Stack ist nicht für die Verwendung in Medizinprodukten, klinischen Studien, klinischen Untersuchungen oder im klinischen Alltag vorgesehen.

This Software Stack is not intended for use in medical products, clinical trials, clinical studies, or in clinical routine.

Anleitung 

MonitorSDCTest – SDC-Prototyp nach IEEE 11073

Dieses Projekt enthält eine funktionsfähige Simulation eines medizinischen SDC Providers und Consumers gemäß dem IEEE 11073-Standard. Die Kommunikation basiert auf der Open-Source-Bibliothek sdc11073 von Drägerwerk.


---

Schnellstart

1. Repository klonen

powershell:

git clone https://github.com/maxhartmann123/Thesis-SDC-Prototyp.git
cd Thesis-SDC-Prototyp

---

2. Virtuelle Umgebung einrichten

powershell:

python -m venv .venv

---

3. Virtuelle Umgebung aktivieren

Empfohlen (CMD statt PowerShell verwenden):

cmd .\.venvScripts\activate.bat

In PowerShell:
powershell:

cmd /k ".\.venv\Scripts\activate.bat"

---

4. Abhängigkeiten installieren

powershell
.\.venv\Scripts\pip.exe install sdc11073

> Optional mit Kompression:

powershell

.\.venv\Scripts\pip.exe install "sdc11073[lz4]"

---

Skript ausführen

#### Variante A: Im Unterordner
powershell
cd Thesis-SDC-Prototyp

..\.\.venv\Scripts\python.exe Simulationstest-functions.py

#### Variante B: Direkt vom Hauptverzeichnis
powershell

.\.venv\Scripts\python.exe Thesis-SDC-Prototyp\Simulationstest-functions.py

---

Bekannte Fehler & Lösungen:

Fehler: `Activate.ps1 kann nicht geladen werden`
plaintext Die Ausführung von Skripts auf diesem System ist deaktiviert.

Lösung: Statt `.ps1` einfach `.bat` aktivieren:

powershell:

cmd /k ".\.venv\Scriptsctivate.bat"

---

Fehler: `ModuleNotFoundError: No module named 'sdc11073'`

Lösung: 
powershell:
.\.venv\Scripts\pip.exe install sdc11073

---

Fehler: `[Errno 2] No such file or directory`

Lösung: Sicherstellen, dass du im richtigen Verzeichnis bist:
powershell: 

cd Thesis-SDC-Prototyp
dir  # zeigt, ob die .py-Datei vorhanden ist

---
Abhängigkeiten

Die wichtigsten Python-Pakete:

- `sdc11073` – SDC-Stack
- `lxml` – XML-Verarbeitung (wird automatisch mit installiert)
- 
---

Hinweis
Dieses Projekt ist ein Demonstrator für Test- und Entwicklungszwecke und wurde im Rahmen einer Thesis Entwickelt.  
Es ist nicht für den klinischen Einsatz gedacht und entspricht nicht IEC 62304 oder MDR-Anforderungen.
---
Kontakt:
Maximilian Hartmann
Projekt: Bachelorarbeit - SDC-Prototyp - KS
Stand: Juli 2025
