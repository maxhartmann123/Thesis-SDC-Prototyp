# Import setup_path to add system Python's site-packages to the path
import setup_path

#!/usr/bin/env python3
"""
Reference Test Consumer für SDC11073.
Verbesserte Version mit plattformunabhängiger Implementierung, besserer Fehlerbehandlung
und konsistenter Konfiguration.
"""

import dataclasses
import enum
import os
import pathlib
import sys
import time
import traceback
import uuid
import platform
import logging
from collections import defaultdict
from concurrent import futures
from decimal import Decimal

import sdc11073.certloader
from sdc11073 import commlog, network, observableproperties
from sdc11073.certloader import mk_ssl_contexts_from_folder
from sdc11073.consumer import SdcConsumer
from sdc11073.definitions_sdc import SdcV1Definitions
from sdc11073.mdib.consumermdib import ConsumerMdib
from sdc11073.mdib.consumermdibxtra import ConsumerMdibMethods
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.xml_types.msg_types import InvocationState
from sdc11073.xml_types import pm_qnames

# Warn-Limit für Bestimmungszeiten
ConsumerMdibMethods.DETERMINATIONTIME_WARN_LIMIT = 2.0

# Konfigurationsparameter aus Umgebungsvariablen oder Standardwerten
discovery_runs = int(os.getenv('ref_discovery_runs', "0"))
discovery_timeout = int(os.getenv('ref_discovery_timeout', "30"))  # Sekunden
operation_timeout = int(os.getenv('ref_operation_timeout', "10"))  # Sekunden
metric_update_wait = int(os.getenv('ref_metric_wait', "20"))  # Sekunden
min_updates_required = int(os.getenv('ref_min_updates', "0"))  # 0 = automatisch (wait_time / 5 - 1)
enable_commlog = os.getenv('ref_enable_commlog', 'true').lower() in ('true', '1', 'yes')


class TestResult(enum.Enum):
    """Ergebnistypen für Tests."""
    PASSED = 'PASSED'
    FAILED = 'FAILED'
    SKIPPED = 'SKIPPED'  # Neuer Status für übersprungene Tests


@dataclasses.dataclass
class TestCollector:
    """Sammler für Testergebnisse mit verbesserter Ausgabe."""
    overall_test_result: TestResult = TestResult.PASSED
    test_messages: list[str] = dataclasses.field(default_factory=list)
    details: dict[str, dict] = dataclasses.field(default_factory=dict)

    def add_result(self, message: str, result: TestResult, details: str = None) -> None:
        """Fügt ein Testergebnis hinzu und aktualisiert Gesamtergebnis."""
        if not isinstance(result, TestResult):
            raise ValueError("Ungültiges TestResult-Objekt")
        
        # Gesamtergebnis aktualisieren (FAILED > SKIPPED > PASSED)
        if self.overall_test_result != TestResult.FAILED:
            if result == TestResult.FAILED:
                self.overall_test_result = TestResult.FAILED
            elif result == TestResult.SKIPPED and self.overall_test_result != TestResult.SKIPPED:
                self.overall_test_result = TestResult.SKIPPED
        
        test_id = message.split(':')[0] if ':' in message else message
        self.test_messages.append(f"{message}: {result.value}")
        
        # Details speichern, wenn vorhanden
        if details:
            self.details[test_id] = {
                'result': result.value,
                'message': message,
                'details': details
            }
    
    def print_summary(self) -> None:
        """Gibt eine formatierte Zusammenfassung der Testergebnisse aus."""
        passed = sum(1 for msg in self.test_messages if 'PASSED' in msg)
        failed = sum(1 for msg in self.test_messages if 'FAILED' in msg)
        skipped = sum(1 for msg in self.test_messages if 'SKIPPED' in msg)
        
        print("\n" + "=" * 50)
        print("TESTERGEBNISSE:")
        print("=" * 50)
        
        # Gesamtergebnis mit Symbol
        symbol = "✅" if self.overall_test_result == TestResult.PASSED else "⚠️" if self.overall_test_result == TestResult.SKIPPED else "❌"
        print(f"{symbol} Gesamtergebnis: {self.overall_test_result.value}")
        print(f"Tests durchgeführt: {len(self.test_messages)} (✅ {passed} bestanden, ❌ {failed} fehlgeschlagen, ⚠️ {skipped} übersprungen)")
        
        # Einzelne Testergebnisse
        print("\nEinzelergebnisse:")
        for msg in self.test_messages:
            if 'PASSED' in msg:
                sym = '✅'
            elif 'FAILED' in msg:
                sym = '❌'
            else:
                sym = '⚠️'
            print(f"{sym} {msg}")
        
        # Details ausgeben, wenn vorhanden
        if self.details and any(r.get('details') for r in self.details.values()):
            print("\nDetails zu fehlgeschlagenen Tests:")
            for test_id, info in self.details.items():
                if info.get('result') == 'FAILED' and info.get('details'):
                    print(f"\n❌ {info['message']}:")
                    print(f"   {info['details']}")


def get_network_adapter() -> network.NetworkAdapter:
    """
    Liefert einen Netzwerkadapter basierend auf Konfiguration.
    Konsistente Implementierung über alle Plattformen.
    """
    logger = logging.getLogger('sdc')
    if (ip := os.getenv('ref_ip')) is not None:
        try:
            return network.get_adapter_containing_ip(ip)
        except Exception as e:
            logger.warning(f"Konnte Adapter mit IP {ip} nicht finden: {e}")
    use_loopback = os.getenv('ref_use_loopback', 'false').lower() in ('true', '1', 'yes')
    try:
        if use_loopback:
            return next(adapter for adapter in network.get_adapters() if adapter.is_loopback)
        else:
            return next(adapter for adapter in network.get_adapters() if not adapter.is_loopback)
    except StopIteration:
        adapters = list(network.get_adapters())
        if not adapters:
            raise RuntimeError("Keine Netzwerkadapter gefunden!")
        logger.warning(
            f"Konnte keinen {'Loopback' if use_loopback else 'Nicht-Loopback'}-Adapter finden. "
            f"Verwende stattdessen: {adapters[0].ip}"
        )
        return adapters[0]


def get_ssl_context() -> sdc11073.certloader.SSLContextContainer | None:
    """
    Liefert SSL-Kontext basierend auf Umgebungsvariablen.
    Verbesserte Fehlerbehandlung und Sicherheit.
    """
    logger = logging.getLogger('sdc')
    if (ca_folder := os.getenv('ref_ca')) is None:
        return None
    
    ssl_passwd = os.getenv('ref_ssl_passwd')
    if ssl_passwd == 'dummypass':
        logger.warning(
            "Verwendung des Standard-SSL-Passworts 'dummypass'. "
            "Für Produktionsumgebungen sollte ein sicheres Passwort verwendet werden."
        )
    try:
        return mk_ssl_contexts_from_folder(
            ca_folder,
            private_key='user_private_key_encrypted.pem',
            certificate='user_certificate_root_signed.pem',
            ca_public_key='root_certificate.pem',
            cyphers_file=None,
            ssl_passwd=ssl_passwd
        )
    except Exception as e:
        logger.exception("SSL-Kontext konnte nicht geladen werden")
        detail = str(e)
        if "Bad decrypt" in detail:
            logger.error("Passwort für SSL-Zertifikate scheint falsch zu sein.")
        elif "No such file or directory" in detail:
            logger.error(f"Zertifikatsdateien nicht gefunden im Verzeichnis: {ca_folder}")
        return None


def get_epr() -> uuid.UUID:
    """Liefert EPR aus Umgebungsvariable oder Standardwert."""
    if (epr := os.getenv('ref_search_epr')):
        return uuid.UUID(epr)
    return uuid.UUID('12345678-6f55-11ea-9697-123456789abc')


def get_commlog_directory() -> str:
    """
    Erstellt und liefert ein plattformunabhängiges Verzeichnis für Kommunikationslogs.
    """
    if os.getenv('ref_commlog_dir'):
        log_dir = os.getenv('ref_commlog_dir')
    else:
        if platform.system() == 'Windows':
            base_dir = os.path.join(os.environ.get('APPDATA', os.path.expanduser('~')), 'SDC11073')
        else:
            base_dir = os.path.join(os.path.expanduser('~'), '.sdc11073')
        log_dir = os.path.join(base_dir, 'commlog')
    os.makedirs(log_dir, exist_ok=True)
    return log_dir


def setup_commlog() -> commlog.DirectoryLogger:
    """
    Konfiguriert und startet Kommunikationslogging.
    Plattformunabhängige Implementation.
    """
    logger = logging.getLogger('sdc')
    log_dir = get_commlog_directory()
    logger.info(f"Kommunikationslogs werden gespeichert in: {log_dir}")
    return commlog.DirectoryLogger(
        log_folder=log_dir,
        log_out=True, 
        log_in=True, 
        broadcast_ip_filter=None
    )


def run_ref_test() -> TestCollector:
    """
    Führt Referenztests durch.
    Verbesserte Fehlerbehandlung, Timeouts und plattformunabhängige Implementierung.
    """
    logger = logging.getLogger('sdc')
    results = TestCollector()

    # Kommunikationslogging starten, wenn aktiviert
    comm_logger = None
    if enable_commlog:
        try:
            comm_logger = setup_commlog()
            logger.info("Kommunikationslogging aktiviert")
        except Exception as e:
            logger.error(f"Fehler beim Starten des Kommunikationsloggings: {e}")

    wsd = None
    client = None

    try:
        # --- Test 1: Service Discovery ---
        adapter = get_network_adapter()
        epr = get_epr()
        print(f"Verwende Netzwerkadapter mit IP: {adapter.ip}")
        print(f"Test 1: Suche Service mit EPR '{epr}'")

        try:
            wsd = WSDiscovery(str(adapter.ip))
            wsd.start()
        except Exception as e:
            error_msg = f"Fehler beim Starten von WSDiscovery: {e}"
            logger.error(error_msg)
            results.add_result("Test 1: Service discovery", TestResult.FAILED, error_msg)
            return results

        service = None
        attempts = 0
        start_time = time.time()
        while service is None and (time.time() - start_time < discovery_timeout):
            try:
                services = wsd.search_services(types=SdcV1Definitions.MedicalDeviceTypesFilter)
                print(f"Gefunden: {len(services)} Services: {[s.epr for s in services]}")
                for s in services:
                    if s.epr.endswith(str(epr)):
                        service = s
                        print(f"Ziel-Service gefunden: {s.epr}")
                        break
                attempts += 1
                if discovery_runs and attempts >= discovery_runs:
                    msg = f"Test 1 fehlgeschlagen: Kein passender Service nach {attempts} Versuchen"
                    results.add_result("Test 1: Service discovery", TestResult.FAILED, msg)
                    return results
                if not service:
                    print(f"Durchlauf {attempts}: Kein passender Service gefunden. Erneuter Versuch...")
                    time.sleep(0.001)
            except Exception as e:
                error_msg = f"Fehler bei der Service-Suche: {e}"
                logger.error(error_msg)
                results.add_result("Test 1: Service discovery", TestResult.FAILED, error_msg)
                return results

        if service is None:
            error_msg = f"Timeout bei Service-Discovery nach {discovery_timeout} Sekunden"
            results.add_result("Test 1: Service discovery", TestResult.FAILED, error_msg)
            return results

        print("Test 1 bestanden: Service erfolgreich gefunden")
        results.add_result("Test 1: Service discovery", TestResult.PASSED)

        # --- Test 2: Connect ---
        print("Test 2: Verbinde mit Gerät")
        try:
            client = SdcConsumer.from_wsd_service(
                service,
                ssl_context_container=get_ssl_context(),
                validate=True
            )
            client.start_all()
            print("Test 2 bestanden: Verbindung hergestellt")
            results.add_result("Test 2: Connect to device", TestResult.PASSED)
        except Exception as e:
            error_msg = f"Fehler beim Verbinden mit dem Gerät: {e}"
            logger.exception(error_msg)
            results.add_result("Test 2: Connect to device", TestResult.FAILED, error_msg)
            return results

        # --- Test 3&4: MDIB init & subscribe ---
        print("Test 3&4: MDIB initialisieren und abonnieren")
        try:
            mdib = ConsumerMdib(client)
            mdib.init_mdib()
            print("Test 3&4 bestanden: MDIB initialisiert und abonniert")
            results.add_result("Test 3: MDIB initialization", TestResult.PASSED)
            results.add_result("Test 4: Subscription setup", TestResult.PASSED)
        except Exception as e:
            error_msg = f"Fehler bei MDIB-Initialisierung/Abonnement: {e}"
            logger.exception(error_msg)
            results.add_result("Test 3: MDIB initialization", TestResult.FAILED, error_msg)
            results.add_result("Test 4: Subscription setup", TestResult.FAILED, error_msg)
            return results

        pm_names = mdib.data_model.pm_names

        # --- Test 5: Patient Context ---
        print("Test 5: Prüfe Patient Context")
        patients = mdib.context_states.NODETYPE.get(pm_names.PatientContextState, [])
        if patients:
            details = ", ".join(f"{p.Handle}" for p in patients)
            results.add_result("Test 5: Patient context", TestResult.PASSED, f"Gefundene Patienten: {details}")
        else:
            results.add_result("Test 5: Patient context", TestResult.FAILED, "Keine Patient Contexts gefunden")

        # --- Test 6: Location Context ---
        print("Test 6: Prüfe Location Context")
        locations = mdib.context_states.NODETYPE.get(pm_names.LocationContextState, [])
        if locations:
            details = ", ".join(f"{l.Handle}" for l in locations)
            results.add_result("Test 6: Location context", TestResult.PASSED, f"Gefundene Locations: {details}")
        else:
            results.add_result("Test 6: Location context", TestResult.FAILED, "Keine Location Contexts gefunden")

        # --- Test 7&8: Updates sammeln und prüfen ---
        metric_updates = defaultdict(list)
        alert_updates = defaultdict(list)

        def on_metric(updates):
            for h, st in updates.items():
                metric_updates[h].append(st)
                if st.MetricValue and st.MetricValue.Value is not None:
                    name = 'Unbekannt'
                    if 'numeric.ch0.vmd0' in h:
                        name = 'HR'
                    elif 'numeric.ch1.vmd0' in h:
                        name = 'SpO2'
                    print(f">>> {name}: {st.MetricValue.Value}")

        def on_alert(updates):
            for h, st in updates.items():
                alert_updates[h].append(st)
                if hasattr(st, 'Presence'):
                    print(f">>> Alarm {h}: {'Aktiv' if st.Presence else 'Inaktiv'}")

        observableproperties.bind(
            mdib,
            metrics_by_handle=on_metric,
            alert_by_handle=on_alert
        )

        wait_time = metric_update_wait
        min_updates = (wait_time // 5 - 1) if min_updates_required == 0 else min_updates_required
        print(f"Warte {wait_time}s auf Updates (min. {min_updates} pro Handle erwartet)")
        start = time.time()
        while time.time() - start < wait_time:
            time.sleep(0.0001)
            if int(time.time() - start) % 5 == 0:
                print(f"Noch {wait_time - int(time.time()-start)}s verbleibend...")

        # Auswertung Metric Updates
        if not metric_updates:
            results.add_result("Test 7: Metric updates", TestResult.FAILED, "Keine Metrik-Updates empfangen")
        else:
            for h, lst in metric_updates.items():
                res = TestResult.PASSED if len(lst) >= min_updates else TestResult.FAILED
                results.add_result(f"Test 7: Metric updates {h}", res,
                                   f"Empfangen: {len(lst)}, Erwartet: {min_updates}")

        # Auswertung Alert Updates
        if not alert_updates:
            results.add_result("Test 8: Alert updates", TestResult.FAILED, "Keine Alarm-Updates empfangen")
        else:
            for h, lst in alert_updates.items():
                res = TestResult.PASSED if len(lst) >= min_updates else TestResult.FAILED
                results.add_result(f"Test 8: Alert updates {h}", res,
                                   f"Empfangen: {len(lst)}, Erwartet: {min_updates}")

        # --- Test 9: Operationen ---
        ops = [
            ('SetString', 'string.ch0.vmd1_sco_0', lambda h: client.set_service_client.set_string(h, 'hoppeldipop')),
            ('SetValue',  'numeric.ch0.vmd1_sco_0', lambda h: client.set_service_client.set_numeric_value(h, Decimal('42'))),
            ('Activate',  'actop.vmd1_sco_0', lambda h: client.set_service_client.activate(h, 'hoppeldipop'))
        ]
        for op_type, handle, call in ops:
            print(f"Test 9: Prüfe {op_type} auf {handle}")
            desc_type = getattr(pm_qnames, f"{op_type}OperationDescriptor", None)
            descs = mdib.descriptions.NODETYPE.get(desc_type, []) if desc_type else []
            if not any(d.Handle == handle for d in descs):
                results.add_result(f"Test 9: {op_type} operation found", TestResult.FAILED,
                                   f"Handle {handle} nicht gefunden")
                continue
            results.add_result(f"Test 9: {op_type} operation found", TestResult.PASSED,
                               f"Handle {handle} gefunden")
            for d in descs:
                if d.Handle != handle:
                    continue
                try:
                    fut = call(d.Handle)
                    res = fut.result(timeout=operation_timeout)
                    ok = res.InvocationInfo.InvocationState == InvocationState.FINISHED
                    results.add_result(f"Test 9: {op_type} {handle}",
                                       TestResult.PASSED if ok else TestResult.FAILED,
                                       f"State={res.InvocationInfo.InvocationState}")
                except futures.TimeoutError:
                    results.add_result(f"Test 9: {op_type} {handle}", TestResult.FAILED,
                                       f"Timeout nach {operation_timeout}s")
                except Exception as e:
                    results.add_result(f"Test 9: {op_type} {handle}", TestResult.FAILED, str(e))

        # --- Test 10: Unsubscribe ---
        print("Test 10: Beende alle Abonnements")
        try:
            if client._subscription_mgr.unsubscribe_all():
                results.add_result("Test 10: Unsubscribe all", TestResult.PASSED)
            else:
                results.add_result("Test 10: Unsubscribe all", TestResult.FAILED,
                                   "Unsubscribe all returned False")
        except Exception as e:
            results.add_result("Test 10: Unsubscribe all", TestResult.FAILED, str(e))

    except Exception as e:
        logger.exception("Unbehandelte Ausnahme im Referenztest")
        results.add_result("Overall Test", TestResult.FAILED, f"Unbehandelte Ausnahme: {e}")

    finally:
        if client:
            try:
                client.stop_all()
                logging.getLogger('sdc').info("SDC Consumer gestoppt")
            except:
                pass
        if comm_logger:
            try:
                comm_logger.stop()
                logging.getLogger('sdc').info("Kommunikationslogging beendet")
            except:
                pass
        if wsd:
            try:
                wsd.stop()
                logging.getLogger('sdc').info("WSDiscovery gestoppt")
            except:
                pass

    return results


def main() -> int:
    """
    Hauptfunktion mit Kommandozeilenargumenten und Rückgabewert.
    """
    import argparse
    
    parser = argparse.ArgumentParser(description="SDC11073 Vitalparameter-Monitor Consumer Test")
    parser.add_argument('--timeout', type=int, default=15,
                        help='Timeout für Service-Discovery in Sekunden (Default: 30)')
    parser.add_argument('--wait', type=int, default=20,
                        help='Wartezeit für Metrik-Updates in Sekunden (Default: 20)')
    parser.add_argument('--min-updates', type=int, default=0,
                        help='Minimale Anzahl an Updates pro Handle (Default: wait/5-1)')
    parser.add_argument('--loopback', action='store_true',
                        help='Loopback-Adapter für lokale Tests verwenden')
    parser.add_argument('--logdir', help='Verzeichnis für Logdateien')
    parser.add_argument('--no-commlog', action='store_true',
                        help='Kommunikationslogging deaktivieren')
    
    args = parser.parse_args()
    
    if args.timeout:
        os.environ['ref_discovery_timeout'] = str(args.timeout)
    if args.wait:
        os.environ['ref_metric_wait'] = str(args.wait)
    if args.min_updates:
        os.environ['ref_min_updates'] = str(args.min_updates)
    if args.loopback:
        os.environ['ref_use_loopback'] = 'true'
    if args.logdir:
        os.environ['ref_commlog_dir'] = args.logdir
    if args.no_commlog:
        os.environ['ref_enable_commlog'] = 'false'
    
    results = run_ref_test()
    results.print_summary()
    return 0 if results.overall_test_result == TestResult.PASSED else 1


if __name__ == '__main__':
    sys.exit(main())

