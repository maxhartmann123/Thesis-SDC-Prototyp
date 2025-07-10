from __future__ import annotations

# Import setup_path to add system Python's site-packages to the path
import setup_path

import decimal
import json
import logging.config
import os
import pathlib
import time
import traceback
import uuid
import random
from typing import TYPE_CHECKING

import sdc11073.certloader
from sdc11073 import location, network, provider, wsdiscovery
from sdc11073.certloader import mk_ssl_contexts_from_folder
from sdc11073.loghelper import LoggerAdapter
from sdc11073.mdib import ProviderMdib
from sdc11073.provider.components import SdcProviderComponents
from sdc11073.provider.servicesfactory import DPWSHostedService, HostedServices, mk_dpws_hosts
from sdc11073.provider.subscriptionmgr_async import SubscriptionsManagerReferenceParamAsync
from sdc11073.xml_types import dpws_types, pm_types
from sdc11073.xml_types import pm_qnames as pm
from sdc11073.xml_types.dpws_types import ThisDeviceType, ThisModelType

if TYPE_CHECKING:
    pass

USE_REFERENCE_PARAMETERS = True

def get_network_adapter() -> network.NetworkAdapter:
    """Get network adapter from environment or first loopback."""
    if (ip := os.getenv('ref_ip')) is not None:
        return network.get_adapter_containing_ip(ip)
    return next(adapter for adapter in network.get_adapters() if adapter.is_loopback)


def get_location() -> location.SdcLocation:
    """Get location from environment or default."""
    return location.SdcLocation(
        fac=os.getenv('ref_fac', 'r_fac'),
        poc=os.getenv('ref_poc', 'r_poc'),
        bed=os.getenv('ref_bed', 'r_bed')
    )


def get_ssl_context() -> sdc11073.certloader.SSLContextContainer | None:
    """Get ssl context from environment or None."""
    if (ca_folder := os.getenv('ref_ca')) is None:
        return None
    return mk_ssl_contexts_from_folder(
        ca_folder,
        private_key='user_private_key_encrypted.pem',
        certificate='user_certificate_root_signed.pem',
        ca_public_key='root_certificate.pem',
        cyphers_file=None,
        ssl_passwd=os.getenv('ref_ssl_passwd')
    )


def get_epr() -> uuid.UUID:
    """Get epr from environment or default."""
    if (epr := os.getenv('ref_search_epr')) is not None:
        return uuid.UUID(epr)
    return uuid.UUID('12345678-6f55-11ea-9697-123456789abc')


def create_reference_provider(
    ws_discovery: wsdiscovery.WSDiscovery | None = None,
    mdib_path: pathlib.Path | None = None,
    dpws_model: dpws_types.ThisModelType | None = None,
    dpws_device: dpws_types.ThisDeviceType | None = None,
    epr: uuid.UUID | None = None,
    specific_components: SdcProviderComponents | None = None,
    ssl_context_container: sdc11073.certloader.SSLContextContainer | None = None
) -> provider.SdcProvider:
    ws_discovery = ws_discovery or wsdiscovery.WSDiscovery(get_network_adapter().ip)
    ws_discovery.start()

    dpws_model = dpws_model or ThisModelType(
        manufacturer='sdc11073',
        manufacturer_url='www.sdc11073.com',
        model_name='VitalParameterMonitor',
        model_number='1.0',
        model_url='www.sdc11073.com/model',
        presentation_url='www.sdc11073.com/model/presentation'
    )
    dpws_device = dpws_device or ThisDeviceType(
        friendly_name='VitalParameterMonitor',
        firmware_version='Version1',
        serial_number='12345'
    )

    mdib = ProviderMdib.from_mdib_file(
        str(mdib_path or pathlib.Path(__file__).parent / 'reference_mdib.xml')
    )

    prov = provider.SdcProvider(
        ws_discovery=ws_discovery,
        this_model=dpws_model,
        this_device=dpws_device,
        device_mdib_container=mdib,
        epr=epr or get_epr(),
        specific_components=specific_components,
        ssl_context_container=ssl_context_container or get_ssl_context(),
    )
    for desc in prov.mdib.descriptions.objects:
        desc.SafetyClassification = pm_types.SafetyClassification.MED_A
    prov.start_all(start_rtsample_loop=True)
    return prov


def set_reference_data(prov: provider.SdcProvider, loc: location.SdcLocation = None):
    loc = loc or get_location()
    prov.set_location(
        loc,
        [pm_types.InstanceIdentifier('Validator', extension_string='System')]
    )
    patient_handle = prov.mdib.descriptions.NODETYPE.get_one(pm.PatientContextDescriptor).Handle
    with prov.mdib.context_state_transaction() as mgr:
        patient_state = mgr.mk_context_state(patient_handle)
        patient_state.CoreData.Givenname = "Max"
        patient_state.CoreData.Middlename = [""]
        patient_state.CoreData.Familyname = "Mustermann"
        patient_state.CoreData.Birthname = "Mustermann"
        patient_state.CoreData.Title = ""
        patient_state.ContextAssociation = pm_types.ContextAssociation.ASSOCIATED
        patient_state.Identification = []


def mk_all_services_except_localization(
    prov: provider.SdcProvider,
    components: SdcProviderComponents,
    subscription_managers: dict
) -> HostedServices:
    dpws_services, services_by_name = mk_dpws_hosts(
        prov, components, DPWSHostedService, subscription_managers
    )
    return HostedServices(
        dpws_services,
        services_by_name['GetService'],
        set_service=services_by_name.get('SetService'),
        context_service=services_by_name.get('ContextService'),
        description_event_service=services_by_name.get('DescriptionEventService'),
        state_event_service=services_by_name.get('StateEventService'),
        waveform_service=services_by_name.get('WaveformService'),
        containment_tree_service=services_by_name.get('ContainmentTreeService')
    )


def setup_logging() -> logging.LoggerAdapter:
    default_cfg = pathlib.Path(__file__).parent / 'logging_default.json'
    if default_cfg.exists():
        logging.config.dictConfig(json.loads(default_cfg.read_bytes()))
    if (extra := os.getenv('ref_xtra_log_cnf')) is not None:
        logging.config.dictConfig(json.loads(pathlib.Path(extra).read_bytes()))
    return LoggerAdapter(logging.getLogger('sdc'))


def generate_heart_rate(last_value=None):
    if last_value is None:
        return decimal.Decimal(random.randint(60, 90))
    variation = random.randint(-3, 3)
    new_value = last_value + variation
    return decimal.Decimal(max(50, min(100, new_value)))


def generate_spo2(last_value=None):
    if last_value is None:
        return decimal.Decimal(random.randint(96, 99))
    variation = random.randint(-1, 1)
    new_value = last_value + variation
    return decimal.Decimal(max(94, min(100, new_value)))


def run_provider():
    logger = setup_logging()
    prov = None
    wsd = None

    try:
        adapter = get_network_adapter()
        wsd = wsdiscovery.WSDiscovery(adapter.ip)
        wsd.start()

        specific_components = (
            SdcProviderComponents(
                subscriptions_manager_class={'StateEvent': SubscriptionsManagerReferenceParamAsync},
                services_factory=mk_all_services_except_localization
            ) if USE_REFERENCE_PARAMETERS else None
        )

        prov = create_reference_provider(
            ws_discovery=wsd,
            specific_components=specific_components
        )
        set_reference_data(prov, get_location())

        heart_rate_metric = prov.mdib.descriptions.handle.get_one('numeric.ch0.vmd0')
        spo2_metric      = prov.mdib.descriptions.handle.get_one('numeric.ch1.vmd0')
        alert_condition  = prov.mdib.descriptions.handle.get_one('ac0.mds0')

        with prov.mdib.metric_state_transaction() as mgr:
            for h in (heart_rate_metric.Handle, spo2_metric.Handle):
                state = mgr.get_state(h)
                if not getattr(state, 'MetricValue', None):
                    state.mk_metric_value()

        logger.info("Provider gestartet. Sendet Vitalparameter (Herzfrequenz und SpO2). CTRL-C zum Beenden")

    except Exception:
        logger.exception("Fehler beim Start des Providers – breche ab")
        if prov:
            prov.stop_all()
        if wsd:
            wsd.stop()
        return

    current_hr   = generate_heart_rate()
    current_spo2 = generate_spo2()
    try:
        while True:
            try:
                with prov.mdib.metric_state_transaction() as mgr:
                    current_hr = generate_heart_rate(current_hr)
                    hr_state   = mgr.get_state(heart_rate_metric.Handle)
                    hr_state.MetricValue.Value = current_hr
                    logger.info(f"Herzfrequenz aktualisiert: {current_hr}")

                with prov.mdib.metric_state_transaction() as mgr:
                    current_spo2 = generate_spo2(current_spo2)
                    sp_state     = mgr.get_state(spo2_metric.Handle)
                    sp_state.MetricValue.Value = current_spo2
                    logger.info(f"SpO2 aktualisiert: {current_spo2}")

            except Exception:
                logger.error(traceback.format_exc())

            try:
                with prov.mdib.alert_state_transaction() as mgr:
                    state = mgr.get_state(alert_condition.Handle)
                    alarm = (current_hr > 95 or current_hr < 55 or current_spo2 < 95)
                    state.Presence = alarm
                    logger.info(f"Alarm gesetzt: {alarm}")
            except Exception:
                logger.error(traceback.format_exc())

            time.sleep(0.0001)

    except KeyboardInterrupt:
        logger.info("Provider wird gestoppt (KeyboardInterrupt)")
    finally:
        if prov:
            prov.stop_all()
        if wsd:
            wsd.stop()