"""Private install-time settings supplied through Unraid's container template."""
import ipaddress
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo
from . import db

def boolean(value):
    val = str(value).strip().lower()
    if val not in ('true', 'false'):
        raise ValueError('Expected true or false')
    return val == 'true'

def positive_int(value):
    val = int(value)
    if val <= 0:
        raise ValueError('Expected positive integer')
    return val

ENV_FIELDS={
    'SENSOR_HOST':('sensor_host',str), 'FORECAST_LATITUDE':('latitude',float),
    'FORECAST_LONGITUDE':('longitude',float), 'LOCATION_ADDRESS':('address',str),
    'FORECAST_ENABLED':('forecast_enabled',boolean), 'TZ':('timezone',str), 'PM_METHOD':('pm_method',str),
    'SENSOR_PLACEMENT':('placement',str), 'ENVIRONMENT_MODE':('environment_mode',str),
    'UNITS':('units',str),
    'PURPLEAIR_API_KEY':('purpleair_api_key',str),
    'PURPLEAIR_SENSOR_INDEX':('purpleair_sensor_index',positive_int),
    'SENSOR_INDEX':('purpleair_sensor_index',positive_int),
    'PURPLEAIR_READ_KEY':('purpleair_read_key',str),
    'SENSOR_SOURCE':('sensor_source',str),
}

def validate(values):
    if 'forecast_enabled' in values and not isinstance(values['forecast_enabled'], bool):
        raise ValueError('Forecast enabled must be a boolean')
    if ('latitude' in values) != ('longitude' in values):
        raise ValueError('Set both forecast coordinates or neither')
    if values.get('sensor_host'):
        ip=ipaddress.ip_address(values['sensor_host'])
        if not ip.is_private or ip.is_loopback or ip.version!=4:
            raise ValueError('SENSOR_HOST must be a private IPv4 address')
    if 'purpleair_sensor_index' in values and values['purpleair_sensor_index'] is not None:
        try:
            val = int(values['purpleair_sensor_index'])
            if val <= 0:
                raise ValueError
            values['purpleair_sensor_index'] = val
        except (ValueError, TypeError):
            raise ValueError('PURPLEAIR_SENSOR_INDEX must be a positive integer')
    if 'purpleair_api_key' in values and values['purpleair_api_key'] is not None:
        val = str(values['purpleair_api_key']).strip()
        if not val:
            raise ValueError('PURPLEAIR_API_KEY must not be empty')
        values['purpleair_api_key'] = val
    if 'purpleair_read_key' in values and values['purpleair_read_key'] is not None:
        values['purpleair_read_key'] = str(values['purpleair_read_key']).strip()
    if 'sensor_source' in values and values['sensor_source'] not in ('local', 'purpleair_api'):
        raise ValueError('Unknown sensor source')
    is_purpleair = values.get('sensor_source') == 'purpleair_api' or (
        bool(values.get('purpleair_sensor_index')) and not values.get('sensor_host') and values.get('sensor_source') != 'local'
    )
    if is_purpleair and not values.get('purpleair_api_key') and not os.environ.get('PURPLEAIR_API_KEY') and not os.environ.get('PURPLEAIR_API_KEY_FILE'):
        raise ValueError('PURPLEAIR_API_KEY is required for PurpleAir API sensor access')

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
    for env_file, key in [('PURPLEAIR_API_KEY_FILE', 'purpleair_api_key'), ('PURPLEAIR_READ_KEY_FILE', 'purpleair_read_key')]:
        if key not in supplied:
            fn = os.environ.get(env_file, '').strip()
            if fn:
                try:
                    supplied[key] = Path(fn).read_text(encoding='utf-8').strip()
                except OSError:
                    raise ValueError(f'Invalid configuration for {env_file}') from None
    merged=db.settings()|supplied
    if ('latitude' in merged) != ('longitude' in merged):
        raise ValueError('Set both forecast coordinates or neither')
    try:
        validate(merged)
    except (ValueError, TypeError, KeyError):
        raise ValueError('Invalid private configuration; check container settings') from None
    db.set_settings(supplied)
