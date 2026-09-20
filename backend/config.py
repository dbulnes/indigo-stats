"""Private install-time settings supplied through Unraid's container template."""
import ipaddress
import math
import os
from zoneinfo import ZoneInfo
from . import db

ENV_FIELDS={
    'SENSOR_HOST':('sensor_host',str), 'FORECAST_LATITUDE':('latitude',float),
    'FORECAST_LONGITUDE':('longitude',float), 'LOCATION_ADDRESS':('address',str),
    'FORECAST_ENABLED':('forecast_enabled',lambda v: v.lower()=='true'), 'TZ':('timezone',str), 'PM_METHOD':('pm_method',str),
    'SENSOR_PLACEMENT':('placement',str), 'ENVIRONMENT_MODE':('environment_mode',str),
}

def validate(values):
    if 'sensor_host' in values:
        ip=ipaddress.ip_address(values['sensor_host'])
        if not ip.is_private or ip.is_loopback or ip.version!=4:
            raise ValueError('SENSOR_HOST must be a private IPv4 address')
    for key,lo,hi in [('latitude',-90,90),('longitude',-180,180),('temperature_offset_f',-30,30),('humidity_offset',-30,30)]:
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
    if 'timezone' in values: ZoneInfo(values['timezone'])
    return values

def import_environment():
    supplied={key:cast(os.environ[env]) for env,(key,cast) in ENV_FIELDS.items() if os.environ.get(env,'').strip()}
    merged=db.settings()|supplied
    if ('latitude' in merged)!=('longitude' in merged):
        raise ValueError('Set both forecast coordinates or neither')
    validate(merged)
    db.set_settings(supplied)
