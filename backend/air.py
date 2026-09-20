"""EPA 2024 PM2.5 breakpoints; distinguish interval estimates from NowCast."""
import math
from datetime import datetime,timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP

BANDS = [(0,9,0,50),(9.1,35.4,51,100),(35.5,55.4,101,150),
         (55.5,125.4,151,200),(125.5,225.4,201,300),(225.5,325.4,301,500)]

def number(value, low=0, high=10000):
    try:
        n=float(value)
        return n if math.isfinite(n) and low <= n <= high else None
    except (ValueError, TypeError):
        return None

def aqi(pm):
    if pm is None or not math.isfinite(pm) or pm < 0:
        return None
    c=Decimal(str(pm)).quantize(Decimal('.1'),rounding=ROUND_DOWN)
    if c > Decimal('325.4'):
        return 500 # API/UI also reports beyond_scale for concentrations above 325.4.
    for lo,hi,ilo,ihi in BANDS:
        lo,hi=Decimal(str(lo)),Decimal(str(hi))
        if lo <= c <= hi:
            return int(((c-lo)*(ihi-ilo)/(hi-lo)+ilo).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
    return None

def environment_values(temp, humidity, mode='purpleair'):
    """Matches PurpleAir map 3.2.5 runtime; raw RH stays separate for PM correction."""
    if mode == 'purpleair':
        return (1.0227*temp-9.375 if temp is not None else None,
                min(100,max(0,1.4498*humidity+7.022)) if humidity is not None else None)
    if mode == 'simple':
        return (temp-8 if temp is not None else None,
                min(100,max(0,humidity+4)) if humidity is not None else None)
    if mode == 'raw': return temp,humidity
    raise ValueError('Unknown environment conversion')

def environment_sql(mode):
    if mode=='purpleair': return '(1.0227*temperature_raw-9.375)', 'MIN(100,MAX(0,1.4498*humidity_raw+7.022))'
    if mode=='simple': return '(temperature_raw-8)', 'MIN(100,MAX(0,humidity_raw+4))'
    if mode=='raw': return 'temperature_raw','humidity_raw'
    raise ValueError('Unknown environment conversion')

def normalize(data, cfg, timestamp):
    if not isinstance(data,dict) or 'SensorId' not in data:
        raise ValueError('Response is not a PurpleAir reading')
    a,b=number(data.get('pm2_5_cf_1')),number(data.get('pm2_5_cf_1_b'))
    tr=number(data.get('current_temp_f'),-100,200)
    hr=number(data.get('current_humidity'),0,100)
    channels=[v for v in (a,b) if v is not None]
    pm=sum(channels)/len(channels) if channels else None
    flags=[]
    source=data.get('DateTime')
    if source:
        try:
            parsed=datetime.fromisoformat(str(source).replace('/','-').replace('z','+00:00').replace('Z','+00:00'))
            if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=timezone.utc)
            if abs(timestamp-parsed.timestamp())>600: flags.append('device_clock_or_stale_source')
        except ValueError:
            flags.append('invalid_device_time')
    if len(channels)<2: flags.append('single_channel' if channels else 'missing_pm')
    if a is not None and b is not None and abs(a-b)>5 and abs(a-b)/max((a+b)/2,1)>.3:
        flags.append('channel_disagreement')
    method=cfg.get('pm_method','cf1')
    if method=='epa2021':
        if pm is None or hr is None:
            pm=None
            flags.append('correction_unavailable')
        elif pm > 500:
            pm=None
            flags.append('outside_correction_range')
        else:
            pm=max(0,.524*pm-.0862*hr+5.75)
    elif method!='cf1':
        raise ValueError('Unsupported PM correction method')
    mode=cfg.get('environment_mode','purpleair')
    temp,humidity=environment_values(tr,hr,mode)
    return dict(ts=int(timestamp)//60*60,source_ts=str(data.get('DateTime',''))[:64],
        temperature=temp,humidity=humidity,environment_mode=mode,
        temperature_raw=tr,humidity_raw=hr,pm_a=a,pm_b=b,pm25=pm,
        method=method,quality=','.join(flags))

def nowcast(hours):
    """12 hourly PM means, newest first; missing hours retain their age/weight."""
    hours=list(hours[:12])
    if len([x for x in hours[:3] if x is not None])<2:
        return None
    valid=[x for x in hours if x is not None]
    maximum=max(valid)
    weight=max(.5,min(valid)/maximum) if maximum else 1
    denominator=sum(weight**i for i,x in enumerate(hours) if x is not None)
    return sum(x*weight**i for i,x in enumerate(hours) if x is not None)/denominator
