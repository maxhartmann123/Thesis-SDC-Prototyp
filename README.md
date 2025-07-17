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


1. Voraussetzungen

- Python 3.8 bis 3.11 (empfohlen) hier wurde Python 3.11.9 benutzt
- Git
- Internetverbindung (für die Paketinstallation)
- Optional: Virtuelle Umgebung (empfohlen)

2. Repository klonen
   
bash:
git clone https://github.com/maxhartmann123/Thesis-SDC-Prototyp.git
cd Thesis-SDC-Prototyp

3. Virtuelle Umgebung einrichten

bash:
python -m venv .venv
source .venv/bin/activate   # unter Windows: .venv\Scripts\activate

4. sdc11073-Bibliothek installieren

bash:
pip install sdc11073
(To update, run: python.exe -m pip install --upgrade pip)
