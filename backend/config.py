"""Private install-time settings supplied through Unraid's container template."""
import ipaddress
import math
import os
from zoneinfo import ZoneInfo
from . import db

def boolean(value):
    val = str(value).strip().lower()
    if val not in ('true', 'false'):
        raise ValueError('Expected true or false')
    return val == 'true'

ENV_FIELDS={
    'SENSOR_HOST':('sensor_host',str), 'FORECAST_LATITUDE':('latitude',float),
    'FORECAST_LONGITUDE':('longitude',float), 'LOCATION_ADDRESS':('address',str),
    'FORECAST_ENABLED':('forecast_enabled',boolean), 'TZ':('timezone',str), 'PM_METHOD':('pm_method',str),
    'SENSOR_PLACEMENT':('placement',str), 'ENVIRONMENT_MODE':('environment_mode',str),
    'UNITS':('units',str),
}

def validate(values):
    if 'forecast_enabled' in values and not isinstance(values['forecast_enabled'], bool):
        raise ValueError('Forecast enabled must be a boolean')
    if ('latitude' in values) != ('longitude' in values):
        raise ValueError('Set both forecast coordinates or neither')
    if 'sensor_host' in values:
        ip=ipaddress.ip_address(values['sensor_host'])
        if not ip.is_private or ip.is_loopback or ip.version!=4:
            raise ValueError('SENSOR_HOST must be a private IPv4 address')
    for key,lo,hi in [('latitude',-90,90),('longitude',-180,180)]:
        if key in values:
            values[key]=float(values[key])
            if not math.isfinite(values[key]) or not lo<=values[key]<=hi:
                raise ValueError('Invalid numeric configuration')
    if values.get('pm_method','cf1') not in ('cf1','epa2021'):
        raise ValueError('Unknown PM correction')
    if values.get('environment_mode','purpleair') not in ('purpleair','raw','simple'):
        raise ValueError('Unknown environment conversion')
    if values.get('placement','outdoors') not in ('outdoors','indoors'):
        raise ValueError('Unknown sensor placement')
    if values.get('units','imperial') not in ('imperial','metric'):
        raise ValueError('Unknown unit system')
    if 'timezone' in values: ZoneInfo(values['timezone'])
    return values

def import_environment():
    supplied={}
    for env,(key,cast) in ENV_FIELDS.items():
        val = os.environ.get(env, '').strip()
        if val:
            try:
                supplied[key] = cast(val)
            except (ValueError, TypeError):
                raise ValueError(f'Invalid configuration for {env}') from None
    merged=db.settings()|supplied
    if ('latitude' in merged) != ('longitude' in merged):
        raise ValueError('Set both forecast coordinates or neither')
    try:
        validate(merged)
    except (ValueError, TypeError, KeyError):
        raise ValueError('Invalid private configuration; check container settings') from None
    db.set_settings(supplied)
