#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SDC11073 Vitalparameter-Monitor Durchsatz- und Jittertest (überarbeitet)

Misst, wie viele Updates pro Sekunde empfangen werden und berechnet die
Intervalldauer (anstelle von Send-Zeitstempel), mit höherer Zeitauflösung
und Vermeidung von 0.0 ms Intervallen.
"""
import os
import sys
import time
import uuid
import threading
import argparse

# Pfad zu System-Python-Paketen hinzufügen
import setup_path
# Lokale Module
import reference_provider
from sdc11073.wsdiscovery import WSDiscovery
from sdc11073.definitions_sdc import SdcV1Definitions
from sdc11073.consumer import SdcConsumer
from sdc11073.mdib.consumermdib import ConsumerMdib
from sdc11073 import observableproperties


def provider_thread_target():
    """
    Provider mit konfigurierbarem Sendeintervall starten.
    Liest ENV ref_send_interval (Sekunden) aus.
    """
    send_interval = float(os.getenv('ref_send_interval', '0.05'))
    os.environ['ref_send_interval'] = str(send_interval)
    reference_provider.run_provider()


def run_performance_consumer(perf_duration: int, discovery_timeout: int):
    """
    Discover Provider, empfängt Metriken für perf_duration Sekunden
    und berechnet Empfangsrate und Intervalldauern.

    Returns:
        Tuple(total_updates, rate_hz, avg_interval_ms, min_interval_ms, max_interval_ms)
    """
    adapter = reference_provider.get_network_adapter()
    wsd = WSDiscovery(str(adapter.ip))
    wsd.start()

    epr = os.getenv('ref_search_epr', '')
    service = None
    start_search = time.time()
    print(f"Suche Service mit EPR {epr} (Timeout {discovery_timeout}s)...")
    while not service and time.time() - start_search < discovery_timeout:
        for s in wsd.search_services(types=SdcV1Definitions.MedicalDeviceTypesFilter):
            if s.epr and s.epr.endswith(epr):
                service = s
                break
        if not service:
            time.sleep(0.00001)
    if not service:
        print("FEHLER: Provider-Service nicht gefunden.")
        wsd.stop()
        sys.exit(1)

    print("Service gefunden, verbinde...")
    client = SdcConsumer.from_wsd_service(
        service,
        ssl_context_container=reference_provider.get_ssl_context(),
        validate=True
    )
    client.start_all()
    mdib = ConsumerMdib(client)
    mdib.init_mdib()

    # Liste von Zeitstempeln in Sekunden (float)
    recv_times: list[float] = []

    def on_perf_metric(updates):
        # Verwende time.perf_counter() für hohe Auflösung
        for _ in updates:
            recv_times.append(time.perf_counter())

    observableproperties.bind(
        mdib,
        metrics_by_handle=on_perf_metric
    )

    print(f"Missperformance für {perf_duration}s...")
    time.sleep(perf_duration)
    client.stop_all()
    wsd.stop()

    if not recv_times:
        print("Keine Metrik-Updates empfangen.")
        sys.exit(1)

    total = len(recv_times)
    rate_hz = total / perf_duration

    # Sortiere und berechne Intervalle in Millisekunden
    recv_times.sort()
    intervals = [
        (t2 - t1) * 1000.0
        for t1, t2 in zip(recv_times, recv_times[1:])
        if t2 > t1  # sichere Filterung, um 0-Differenzen zu vermeiden
    ]
    avg_int = sum(intervals) / len(intervals) if intervals else 0.0
    min_int = min(intervals) if intervals else 0.0
    max_int = max(intervals) if intervals else 0.0
    return total, rate_hz, avg_int, min_int, max_int


def main():
    parser = argparse.ArgumentParser(
        description='Durchsatz- und Jittertest für SDC11073 Monitor'
    )
    parser.add_argument('--timeout', type=int, default=10,
                        help='Timeout Provider-Start in Sekunden')
    parser.add_argument('--send-interval', type=float, default=0.5,
                        help='Provider Sendeintervall in Sekunden')
    parser.add_argument('--perf-duration', type=int, default=5,
                        help='Messdauer in Sekunden')
    parser.add_argument('--loopback', action='store_true', help='Loopback verwenden')
    parser.add_argument('--logdir', help='Verzeichnis für Logs')
    parser.add_argument('--ssl-passwd', help='SSL Passwort')
    args = parser.parse_args()

    os.environ['ref_search_epr'] = str(uuid.uuid4())
    os.environ['ref_use_loopback'] = 'true' if args.loopback else 'false'
    os.environ['ref_send_interval'] = str(args.send_interval)
    if args.logdir:
        os.environ['ref_log_dir'] = args.logdir
        os.environ['ref_commlog_dir'] = args.logdir

    print("Starte Provider-Thread...")
    thread = threading.Thread(target=provider_thread_target, daemon=True)
    thread.start()
    print(f"Warte bis zu {args.timeout}s auf Provider-Start...")
    time.sleep(args.timeout)

    total, rate_hz, avg_int, min_int, max_int = run_performance_consumer(
        perf_duration=args.perf_duration,
        discovery_timeout=args.timeout
    )

    print("\n--- ERGEBNISSE ---")
    print(f"Gesamt-Updates: {total}")
    print(f"Rate: {rate_hz:.1f} Updates/s")
    print(f"Intervall ms: avg={avg_int:.1f}, min={min_int:.1f}, max={max_int:.1f}")
    sys.exit(0)

if __name__ == '__main__':
    main()

