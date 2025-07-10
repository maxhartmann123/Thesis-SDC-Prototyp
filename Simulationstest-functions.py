#!/usr/bin/env python3
# Import setup_path to add system Python's site-packages to the path
import setup_path


import argparse
import os
import pathlib
import platform
import sys
import threading
import time
import uuid
import logging
import signal
from typing import Optional

# Import der lokalen Module
import reference_provider
import reference_consumer


def set_environment_variables(
    tls: bool,
    use_loopback: bool,
    log_dir: Optional[str],
    ssl_passwd: Optional[str]
) -> None:
    """
    Konfiguriert Umgebungsvariablen für die Tests.
    Plattformunabhängig und mit verbesserter Fehlerbehandlung.
    """
    # Erzeuge eine eindeutige Provider EPR ID
    os.environ['ref_search_epr'] = str(uuid.uuid4())
    
    # Loopback-Konfiguration
    os.environ['ref_use_loopback'] = 'true' if use_loopback else 'false'
    
    # Versuche, einen passenden Netzwerkadapter zu finden
    try:
        # Loopback-Modus wird jetzt über die dedizierte Variable gesteuert
        # Dadurch entfällt die plattformspezifische Logik hier
        adapter = reference_provider.get_network_adapter()
        os.environ['ref_ip'] = str(adapter.ip)
    except Exception as e:
        print(f"WARNUNG: Problem bei Netzwerkadapter-Auswahl: {e}")
        print("Benutze Standardeinstellungen. Dies könnte Verbindungsprobleme verursachen.")
    
    # Log-Verzeichnis konfigurieren
    if log_dir:
        os.environ['ref_log_dir'] = log_dir
        os.environ['ref_commlog_dir'] = log_dir
    
    # TLS-Konfiguration
    if tls:
        # Suche nach Zertifikaten in verschiedenen möglichen Pfaden
        cert_paths = [
            pathlib.Path(__file__).parent / 'certs',                      # ./certs
            pathlib.Path(__file__).parent.parent / 'certs',               # ../certs
            pathlib.Path(__file__).parent.parent.parent / 'certs',        # ../../certs
            pathlib.Path.home() / '.sdc11073' / 'certs',                  # ~/.sdc11073/certs
        ]
        
        if platform.system() == 'Windows':
            cert_paths.append(pathlib.Path(os.environ.get('APPDATA', '')) / 'SDC11073' / 'certs')
        
        # Finde den ersten existierenden Pfad
        certs_path = None
        for path in cert_paths:
            if path.exists() and path.is_dir():
                certs_path = path
                break
        
        if not certs_path:
            paths_str = "\n  - ".join([str(p) for p in cert_paths])
            print(f"FEHLER: Kein Zertifikate-Ordner gefunden! Gesucht in:\n  - {paths_str}")
            print("Bitte stelle sicher, dass der Ordner 'certs' existiert und Zertifikate enthält.")
            sys.exit(1)
        
        # TLS-Konfiguration setzen
        os.environ['ref_ca'] = str(certs_path)
        
        # SSL-Passwort setzen (aus Argument oder Standard)
        os.environ['ref_ssl_passwd'] = ssl_passwd or os.getenv('ref_ssl_passwd', 'dummypass')
        
        if os.environ['ref_ssl_passwd'] == 'dummypass':
            print("WARNUNG: Verwende Standard-SSL-Passwort 'dummypass'. "
                  "In Produktionsumgebungen bitte ein sicheres Passwort verwenden.")
    
    # Konfiguration ausgeben
    print("Konfiguration:")
    print(f"  - Provider ID: {os.environ['ref_search_epr']}")
    print(f"  - Netzwerk-IP: {os.environ['ref_ip']}")
    print(f"  - Loopback-Modus: {'aktiviert' if use_loopback else 'deaktiviert'}")
    
    # TLS-Ausgabe
    tls_status = 'aktiviert' if tls else 'deaktiviert'
    cert_info = f" mit Zertifikaten aus {os.environ.get('ref_ca')}" if tls else ''
    print(f"  - TLS {tls_status}{cert_info}")
    
    # Logverzeichnis ausgeben
    if 'ref_log_dir' in os.environ:
        print(f"  - Log-Verzeichnis: {os.environ['ref_log_dir']}")


def provider_thread_target(ready_event=None):
    """
    Startet den Provider in einem separaten Thread.
    Verbessert mit Event für Bereitschaftssignalisierung.
    """
    try:
        # Start des Providers mit Ausnahmebehandlung im Inneren
        reference_provider.run_provider()
    except Exception as e:
        print(f"FEHLER im Provider-Thread: {e}")
        if ready_event:
            ready_event.set()  # Signal senden, dass Thread beendet wurde (mit Fehler)


def run(tls: bool, timeout: int = 10) -> reference_consumer.TestCollector:
    """
    Startet den Provider im Hintergrund und führt dann Consumer-Tests aus.
    Verbessert mit Timeout-Handling und Bereitschaftssignalisierung.
    
    Args:
        tls: TLS-Verschlüsselung aktivieren
        timeout: Timeout für Provider-Start in Sekunden
        
    Returns:
        TestCollector mit Testergebnissen
    """
    print("\n--- VITALPARAMETER-MONITOR: TESTSUITE WIRD GESTARTET ---\n")
    
    # Event für die Synchronisation (Provider ist bereit oder fehlgeschlagen)
    ready_event = threading.Event()
    
    print("Starte Provider in separatem Thread...")
    thread = threading.Thread(
        target=provider_thread_target,
        args=(ready_event,),
        daemon=True
    )
    thread.start()
    
    # Warte auf Provider-Start
    print(f"Warte maximal {timeout} Sekunden auf Provider-Start...")
    start_time = time.time()
    
    # Verbesserte Statusüberprüfung mit Timeout
    while time.time() - start_time < timeout:
        # Prüfe, ob Thread noch läuft
        if not thread.is_alive():
            print("\nFEHLER: Provider-Thread wurde unerwartet beendet!")
            sys.exit(1)
        
        # Prüfe, ob das Ready-Event signalisiert wurde (Fehler)
        if ready_event.is_set():
            print("\nFEHLER: Provider hat einen Fehler gemeldet!")
            sys.exit(1)
        
        # Kurze Pause
        time.sleep(2)
        # Nach 3 Sekunden anfangen, auf Provider-Bereitschaft zu prüfen
        # Diese Eigenschaften müssten in der Provider-Implementierung ergänzt werden
        elapsed = time.time() - start_time
        if elapsed >= 3:
            try:
                # Der Provider sollte eine Flag setzen, wenn er bereit ist
                if hasattr(reference_provider, 'provider_ready') and reference_provider.provider_ready:
                    print(f"Provider erfolgreich gestartet nach {elapsed:.1f} Sekunden!")
                    break
            except Exception:
                # Ignoriere Fehler bei der Statusabfrage
                pass
    
    # Timeout-Prüfung
    if time.time() - start_time >= timeout:
        print(f"\nWARNUNG: Timeout ({timeout}s) beim Warten auf Provider-Start!")
        print("Versuche trotzdem, mit den Tests fortzufahren...")
    
    # Starte Consumer und Tests
    print("Starte Consumer und Tests...")
    return reference_consumer.run_ref_test()


def main(
    tls: bool,
    use_loopback: bool = False,
    log_dir: Optional[str] = None,
    ssl_passwd: Optional[str] = None,
    timeout: int = 7,
    wait_time: int = 10,
    min_updates: int = 0
) -> int:
    """
    Haupteinstiegspunkt: Umgebung einrichten, Provider+Consumer-Tests ausführen, Zusammenfassung ausgeben.
    
    Args:
        tls: TLS-Verschlüsselung aktivieren
        use_loopback: Loopback-Netzwerkadapter verwenden
        log_dir: Verzeichnis für Logs (optional)
        ssl_passwd: Passwort für SSL-Zertifikate (optional)
        timeout: Timeout für Provider-Start in Sekunden
        wait_time: Wartezeit für Metrik-Updates in Sekunden
        min_updates: Minimale Anzahl an Updates pro Handle
        
    Returns:
        Exit-Code (0 = erfolgreich, 1 = fehlgeschlagen)
    """
    print("=" * 50)
    print("SDC11073 VITALPARAMETER-MONITOR TESTSUITE")
    print("=" * 50)
    
    # Umgebungsvariablen für Consumer-Tests setzen
    if wait_time > 0:
        os.environ['ref_metric_wait'] = str(wait_time)
    if min_updates > 0:
        os.environ['ref_min_updates'] = str(min_updates)
    
    # SIGINT-Handler zum sauberen Beenden
    original_sigint = signal.getsignal(signal.SIGINT)
    
    def sigint_handler(sig, frame):
        print("\nTests werden auf Benutzeranfrage beendet...")
        sys.exit(2)
    
    signal.signal(signal.SIGINT, sigint_handler)
    
    try:
        # Umgebung einrichten
        set_environment_variables(
            tls=tls,
            use_loopback=use_loopback,
            log_dir=log_dir,
            ssl_passwd=ssl_passwd
        )
        
        # Tests ausführen
        results = run(tls, timeout)
        
        # Ausführliche Zusammenfassung ausgeben
        for msg in results.test_messages:
            print(msg)

        
        # Rückgabewert basierend auf Testergebnis
        overall = results.overall_test_result
        return 0 if overall == reference_consumer.TestResult.PASSED else 1
    
    finally:
        # Original-SIGINT-Handler wiederherstellen
        signal.signal(signal.SIGINT, original_sigint)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Vitalparameter-Monitor: SDC11073 Plug-a-thon Tests'
    )
    
    # Grundlegende Optionen
    parser.add_argument('--tls', action='store_true',
                       help='Aktiviert TLS-Verschlüsselung für die Tests')
    parser.add_argument('--loopback', action='store_true',
                       help='Verwendet Loopback-Netzwerkadapter (für lokale Tests)')
    
    # Verzeichnisse und Pfade
    parser.add_argument('--logdir', 
                       help='Verzeichnis für Log-Dateien (Standard: plattformspezifisch)')
    
    # TLS-Optionen
    parser.add_argument('--ssl-passwd',
                       help='Passwort für SSL-Zertifikate (Standard: Umgebungsvariable oder "dummypass")')
    
    # Timeout und Wartezeiten
    parser.add_argument('--timeout', type=int, default=10,
                       help='Timeout für Provider-Start in Sekunden (Standard: 15)')
    parser.add_argument('--wait', type=int, default=10,
                       help='Wartezeit für Metrik-Updates in Sekunden (Standard: 20)')
    parser.add_argument('--min-updates', type=int, default=0,
                       help='Minimale Anzahl an Updates pro Handle (Standard: wait/5-1)')
    
    # Kommandozeilenargumente parsen
    args = parser.parse_args()
    
    # Hauptfunktion aufrufen
    exit_code = main(
        tls=args.tls,
        use_loopback=args.loopback,
        log_dir=args.logdir,
        ssl_passwd=args.ssl_passwd,
        timeout=args.timeout,
        wait_time=args.wait,
        min_updates=args.min_updates
    )
    
    # Ausgabe des Ergebnisses
    print(f"\nBeende mit Exit-Code: {exit_code} (0 = erfolgreich, 1 = fehlgeschlagen, 2 = abgebrochen)")
    sys.exit(exit_code)
