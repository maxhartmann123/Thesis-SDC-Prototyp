#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
failure_test.py

–– A “failure‐injection” SDC11073 provider for HR/SpO2. ––

  • Sends HR (heart rate) and SpO2 every ~0.5 s.
  • Every few cycles, attempts to inject a “bad” float (instead of Decimal).
  • Every ~5 s, simulates a short connection drop + restart.
  • Runs for exactly 30 s, then prints a summary of injected/failure counts.
"""

import threading
import time
import uuid
import logging
from decimal import Decimal

# -----------------------------------------------------------------------------
# 1) imports from sdc11073
# -----------------------------------------------------------------------------
from sdc11073.provider.providerimpl import SdcProvider
from sdc11073.provider.mdib.providermdib import MdibFactory
from sdc11073.wsdiscovery.wsdimpl import WSDiscovery
from sdc11073.xml_types import pm_types, pm_storage, pm_definitions  # for metric/alert descriptors
from sdc11073.network import get_ip_for_loopback
from sdc11073.types import stop_event  # a shared event to signal provider to stop
# -----------------------------------------------------------------------------

LOGGER = logging.getLogger('failure_test')
LOGGER.setLevel(logging.INFO)
ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
LOGGER.addHandler(ch)

# -----------------------------------------------------------------------------
# Global counters (incremented inside provider thread)
# -----------------------------------------------------------------------------
_bad_float_writes = 0
_reconnects = 0

# -----------------------------------------------------------------------------
# 2) Locate and load the reference MDIB file
# -----------------------------------------------------------------------------
THIS_DIR = __import__('pathlib').Path(__file__).parent
MDIB_PATH = THIS_DIR / 'reference_mdib.xml'

if not MDIB_PATH.exists():
    LOGGER.error(f"MDIB file not found at {MDIB_PATH!s}. Please place reference_mdib.xml here.")
    raise SystemExit(1)

# -----------------------------------------------------------------------------
# 3) Build a “provider” factory function
# -----------------------------------------------------------------------------
def create_reference_provider(use_loopback: bool):
    """
    1) Start WS‐Discovery on either loopback or the “best” adapter.
    2) Load the MDIB from disk via MdibFactory.
    3) Instantiate SdcProvider(mdib, wsd, <device‐type>, …).
    """
    # 3.1) Pick the IP to bind WS‐Discovery to
    if use_loopback:
        ip_address = '127.0.0.1'
    else:
        try:
            ip_address = get_ip_for_loopback(False)  # picks the “main” adapter IP
        except Exception:
            ip_address = '127.0.0.1'

    # 3.2) Start WS‐Discovery (requires ip_address now)
    wsd = WSDiscovery(ip_address=ip_address)

    # 3.3) Load MDIB from file
    mdib = MdibFactory.from_file(str(MDIB_PATH))

    # 3.4) Create SdcProvider:
    #       – mdib: the MDIB object we just loaded
    #       – wsd: the WS‐Discovery instance
    #       – deviceTypeFilter: we only provide “Vital Signs Monitor”
    device_types = [pm_definitions.MedicalDeviceTypesFilter]
    prov = SdcProvider(mdib, wsd, device_types)

    # 3.5) Configure “PatientContext” so the MDIB has at least one Patient
    #       (some older examples used pm_types.PatientContextDescriptor, but
    #        in newer versions that attribute may not be there; instead, you
    #        can just grab the first PatientContext from the MDIB itself)
    try:
        # pick any PatientContextDescriptor from MDIB
        patient_ctx = mdib.descriptions.NODETYPE.get_one(pm_types.PatientContextDescriptor)
        root_handle = patient_ctx.Handle
        prov.set_location(root_handle, [pm_types.InstanceIdentifier('FailureTest', extension_string='System')])
    except Exception as e:
        # if PatientContextDescriptor isn’t present, skip explicit location
        LOGGER.warning(f"(Could not set location explicitly: {e})")

    return prov, wsd


# -----------------------------------------------------------------------------
# 4) In‐loop “send HR/SpO2 + occasionally bad floats + simulated reconnects”
# -----------------------------------------------------------------------------
def run_provider_loop(prov: SdcProvider, wsd: WSDiscovery, run_time: float):
    """
    – Updates HR + SpO2 metrics every 0.5 s.
    – Every 5 s, force a short “stop” and then restart the provider (simulate drop).
    – Occasionally write a bad float into the MDIB → catch ValueError and log it.
    – Continue for exactly run_time seconds, then set stop_event so everything stops.
    """
    global _bad_float_writes, _reconnects

    mdib = prov.mdib
    start_ts = time.time()
    last_reconnect = start_ts

    # Fetch the existing Metric‐Descriptors from MDIB
    # (assumes IDs “HR/Metric/Descriptor” and “SpO2/Metric/Descriptor” exist in reference_mdib.xml)
    try:
        hr_descr = mdib.descriptions.nodes.get_one(pm_types.MetricDescriptor, handle='HR/Metric/Descriptor')
        spo2_descr = mdib.descriptions.nodes.get_one(pm_types.MetricDescriptor, handle='SpO2/Metric/Descriptor')
    except Exception:
        LOGGER.error("Could not find HR/SpO2 MetricDescriptors by those handles in MDIB.")
        raise

    # Cache the handles to later update their MetricState
    hr_hdl = hr_descr.Handle
    spo2_hdl = spo2_descr.Handle

    while True:
        now = time.time()
        if now - start_ts > run_time:
            stop_event.set()  # signal provider & WS‐Discovery to shut down
            return

        # 4.1) Every ~5 s: simulate a reconnect
        if now - last_reconnect >= 5.0:
            _reconnects += 1
            LOGGER.warning("Simulated connection drop: stopping provider briefly…")
            prov.stop()
            wsd.stop()
            time.sleep(0.5)  # half‐second outage
            # restart WSDiscovery + provider
            wsd.start()
            prov.start()
            last_reconnect = now
            LOGGER.info("Provider restarted")
            # skip sending metrics immediately at reconnect
            time.sleep(0.1)

        # 4.2) Send a normal HR (Decimal) and SpO2 (Decimal)
        hr_value = Decimal(60 + (now - start_ts) % 40)  # some varying heart‐rate
        spo2_value = Decimal(95 + ((now - start_ts) * 0.1) % 3)  # some varying SpO2

        # Update MetricState for HR
        try:
            with mdib.metric_state_transaction() as mgr:
                hr_node = mgr.get_descriptor_node(hr_hdl)
                hr_node.MetricValue.Value = hr_value
                LOGGER.info(f"HR sent: {hr_value}")
        except Exception as e:
            # Catch any error (e.g. wrong type) but do not dump entire stack
            LOGGER.error(f"Error updating HR metric: {e}")

        # Update MetricState for SpO2
        try:
            with mdib.metric_state_transaction() as mgr:
                spo2_node = mgr.get_descriptor_node(spo2_hdl)
                spo2_node.MetricValue.Value = spo2_value
                LOGGER.info(f"SpO2 sent: {spo2_value}")
        except Exception as e:
            LOGGER.error(f"Error updating SpO2 metric: {e}")

        # 4.3) Occasionally inject a “bad” float for HR (instead of Decimal)
        #       (every 0.5 s loop, do this ~1 in 5 times)
        if int((now - start_ts) * 10) % 5 == 0:
            bad = float(hr_value)
            try:
                with mdib.metric_state_transaction() as mgr:
                    node = mgr.get_descriptor_node(hr_hdl)
                    node.MetricValue.Value = bad  # intentionally wrong type
                    # If the library rejects floats, we’ll catch below
                    LOGGER.info(f"(Injected bad float for HR: {bad})")
            except Exception as e:
                _bad_float_writes += 1
                LOGGER.error(f"Injected‐float HR write failed: {e}")

        # 4.4) Advance about 0.5 s per loop
        time.sleep(0.5)


# -----------------------------------------------------------------------------
# 5) “Main” – start provider in a background thread, run loop for 30 s, then summarize
# -----------------------------------------------------------------------------
def main():
    run_duration = 30.0  # run exactly 30 seconds

    use_loopback = True  # force loopback for simplicity
    try:
        prov, wsd = create_reference_provider(use_loopback)
    except Exception as e:
        LOGGER.error(f"Failed to create provider: {e}")
        return 1

    # Start WS‐Discovery + provider
    wsd.start()
    prov.start()
    LOGGER.info("Provider is now running (Loopback mode)")

    # Run the update loop in a separate thread
    t = threading.Thread(
        target=lambda: run_provider_loop(prov, wsd, run_duration),
        daemon=True
    )
    t.start()

    # Wait until either 30 s elapses or user Ctrl+C
    try:
        t.join(timeout=run_duration + 1.0)
    except KeyboardInterrupt:
        LOGGER.warning("User requested shutdown.")
        stop_event.set()

    # Stop everything cleanly
    prov.stop()
    wsd.stop()
    LOGGER.info("Provider & WS‐Discovery have been stopped.")

    # --------------- Print a short summary ---------------
    print("")
    print("========== TEST SUMMARY ==========")
    print(f"Total run time: {run_duration:.1f} s")
    print(f"Injected bad‐float writes (caught): {_bad_float_writes}")
    print(f"Simulated reconnects: {_reconnects}")
    print("==================================")
    return 0


if __name__ == "__main__":
    exit_code = main()
    raise SystemExit(exit_code)
