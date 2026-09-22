"""
Pure-Python astronomical ephemeris and topocentric celestial calculations.
Provides moon statistics, topocentric coordinates (Alt/Az), visible naked-eye planets,
meteor showers with moon-interference ratings, celestial events, and stargazing quality index.
All calculations operate locally and privately with zero external network or C dependencies.
"""
from __future__ import annotations
import math
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

DEG_TO_RAD = math.pi / 180.0
RAD_TO_DEG = 180.0 / math.pi


def _norm_deg(deg: float) -> float:
    """Normalize angle in degrees to [0, 360)."""
    return deg % 360.0


def _norm_rad(rad: float) -> float:
    """Normalize angle in radians to [0, 2*pi)."""
    return rad % (2.0 * math.pi)


def julian_day(ts: float) -> float:
    """Convert Unix timestamp (seconds) to Julian Day number."""
    return (ts / 86400.0) + 2440587.5


def julian_centuries(jd: float) -> float:
    """Julian centuries since J2000.0."""
    return (jd - 2451545.0) / 36525.0


def gmst_deg(jd: float) -> float:
    """Greenwich Mean Sidereal Time in degrees."""
    t = julian_centuries(jd)
    # IAU 1982 formula for GMST
    theta = 280.46061837 + 360.98564736629 * (jd - 2451545.0) + 0.000387933 * t * t - (t**3) / 38710000.0
    return _norm_deg(theta)


def local_sidereal_time_deg(jd: float, lon_deg: float) -> float:
    """Local Sidereal Time in degrees for given longitude (East positive)."""
    return _norm_deg(gmst_deg(jd) + lon_deg)


def obliquity_deg(t: float) -> float:
    """Mean obliquity of the ecliptic in degrees."""
    return 23.439291 - 0.0130042 * t - 0.00000016 * t * t


def sun_ecliptic_pos(t: float) -> Tuple[float, float, float]:
    """
    Returns (longitude_deg, latitude_deg, distance_AU) of the Sun.
    """
    l0 = 280.46646 + 36000.76983 * t + 0.0003032 * t * t
    m = 357.52911 + 35999.05029 * t - 0.0001537 * t * t
    m_rad = _norm_deg(m) * DEG_TO_RAD
    c = ((1.914602 - 0.004817 * t - 0.000014 * t * t) * math.sin(m_rad)
         + (0.019993 - 0.000101 * t) * math.sin(2.0 * m_rad)
         + 0.000289 * math.sin(3.0 * m_rad))
    sun_lon = _norm_deg(l0 + c)
    v = m_rad + c * DEG_TO_RAD
    e = 0.016708634 - 0.000042037 * t
    r = (1.000001018 * (1.0 - e * e)) / (1.0 + e * math.cos(v))
    return sun_lon, 0.0, r


def ecliptic_to_equatorial(lon_deg: float, lat_deg: float, eps_deg: float) -> Tuple[float, float]:
    """
    Convert ecliptic coordinates (lon, lat) to equatorial (RA, Dec) in degrees.
    """
    l_rad = lon_deg * DEG_TO_RAD
    b_rad = lat_deg * DEG_TO_RAD
    e_rad = eps_deg * DEG_TO_RAD

    sin_dec = math.sin(b_rad) * math.cos(e_rad) + math.cos(b_rad) * math.sin(e_rad) * math.sin(l_rad)
    dec = math.asin(max(-1.0, min(1.0, sin_dec))) * RAD_TO_DEG

    y = math.sin(l_rad) * math.cos(e_rad) - math.tan(b_rad) * math.sin(e_rad)
    x = math.cos(l_rad)
    ra = _norm_deg(math.atan2(y, x) * RAD_TO_DEG)
    return ra, dec


def equatorial_to_horizontal(ra_deg: float, dec_deg: float, lat_deg: float, lst_deg: float) -> Tuple[float, float]:
    """
    Convert equatorial (RA, Dec) to topocentric horizontal (Altitude, Azimuth) in degrees.
    Azimuth is measured from North through East (0° = North, 90° = East, 180° = South, 270° = West).
    """
    h_angle_rad = _norm_deg(lst_deg - ra_deg) * DEG_TO_RAD
    lat_rad = lat_deg * DEG_TO_RAD
    dec_rad = dec_deg * DEG_TO_RAD

    sin_alt = math.sin(lat_rad) * math.sin(dec_rad) + math.cos(lat_rad) * math.cos(dec_rad) * math.cos(h_angle_rad)
    alt_rad = math.asin(max(-1.0, min(1.0, sin_alt)))

    cos_az = (math.sin(dec_rad) - math.sin(lat_rad) * math.sin(alt_rad)) / (math.cos(lat_rad) * math.cos(alt_rad) + 1e-12)
    sin_az = -math.cos(dec_rad) * math.sin(h_angle_rad) / (math.cos(alt_rad) + 1e-12)
    az_deg = _norm_deg(math.atan2(sin_az, cos_az) * RAD_TO_DEG)

    return alt_rad * RAD_TO_DEG, az_deg


def atmospheric_refraction_deg(alt_deg: float) -> float:
    """Atmospheric refraction in degrees for apparent altitude."""
    if alt_deg < -1.0:
        return 0.0
    r_arcmin = 1.02 / math.tan((alt_deg + 10.3 / (alt_deg + 5.11)) * DEG_TO_RAD)
    return r_arcmin / 60.0


def azimuth_to_cardinal(az_deg: float) -> str:
    """Convert azimuth angle (0-360) to 16-point cardinal compass string."""
    dirs = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
            'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW']
    idx = int(round((az_deg % 360.0) / 22.5)) % 16
    return dirs[idx]


def get_constellation(ra_deg: float, dec_deg: float) -> str:
    """
    Simplified IAU constellation mapping for major ecliptic and night sky constellations.
    """
    # Zodiac constellations by Right Ascension boundaries along the ecliptic
    zodiac_boundaries = [
        (0.0, 30.0, 'Pisces'),
        (30.0, 60.0, 'Aries'),
        (60.0, 90.0, 'Taurus'),
        (90.0, 120.0, 'Gemini'),
        (120.0, 150.0, 'Cancer'),
        (150.0, 180.0, 'Leo'),
        (180.0, 210.0, 'Virgo'),
        (210.0, 240.0, 'Libra'),
        (240.0, 270.0, 'Scorpius'),
        (270.0, 300.0, 'Sagittarius'),
        (300.0, 330.0, 'Capricornus'),
        (330.0, 360.0, 'Aquarius'),
    ]
    # Check if close to ecliptic (Dec between -30 and +30)
    for start, end, name in zodiac_boundaries:
        if start <= ra_deg < end:
            return name
    return 'Pisces'


def moon_position_details(ts: float, lat_deg: float, lon_deg: float) -> Dict[str, Any]:
    """
    Compute comprehensive topocentric Moon coordinates and phase telemetry.
    Based on truncated Meeus Ch. 47 series with topocentric parallax and refraction.
    """
    jd = julian_day(ts)
    t = julian_centuries(jd)
    eps = obliquity_deg(t)
    lst = local_sidereal_time_deg(jd, lon_deg)

    # Fundamental arguments (degrees)
    lp = _norm_deg(218.3164477 + 481267.88123421 * t - 0.0015786 * t * t)
    d = _norm_deg(297.8501921 + 445267.1114034 * t - 0.0018819 * t * t)
    m = _norm_deg(357.5291092 + 35999.0502909 * t - 0.0001536 * t * t)
    mp = _norm_deg(134.9633964 + 477198.8675055 * t + 0.0087414 * t * t)
    f = _norm_deg(93.2720950 + 483202.0175233 * t - 0.0036539 * t * t)

    def s(deg: float) -> float:
        return math.sin(deg * DEG_TO_RAD)

    def c(deg: float) -> float:
        return math.cos(deg * DEG_TO_RAD)

    # Periodic perturbations for ecliptic longitude
    sigma_l = (
        6.288774 * s(mp)
        + 1.274027 * s(2.0 * d - mp)
        + 0.658314 * s(2.0 * d)
        + 0.213618 * s(2.0 * mp)
        - 0.185116 * s(m)
        - 0.114332 * s(2.0 * f)
        + 0.058793 * s(2.0 * d - 2.0 * mp)
        + 0.057066 * s(2.0 * d - m - mp)
        + 0.053322 * s(2.0 * d + mp)
        + 0.045758 * s(2.0 * d - m)
        - 0.040923 * s(m - mp)
        - 0.034720 * s(d)
        - 0.030383 * s(m + mp)
        + 0.015327 * s(2.0 * d - 2.0 * f)
        - 0.012528 * s(2.0 * f + mp)
        + 0.010980 * s(2.0 * f - mp)
        + 0.010675 * s(4.0 * d - mp)
        + 0.010034 * s(3.0 * mp)
    )

    # Periodic perturbations for ecliptic latitude
    sigma_b = (
        5.128122 * s(f)
        + 0.280606 * s(mp + f)
        + 0.277693 * s(mp - f)
        + 0.173238 * s(2.0 * d - f)
        + 0.055413 * s(2.0 * d - mp + f)
        + 0.046271 * s(2.0 * d - mp - f)
        + 0.032573 * s(2.0 * d + f)
        + 0.017198 * s(2.0 * mp + f)
        + 0.009266 * s(2.0 * d + mp - f)
        + 0.008822 * s(2.0 * mp - f)
    )

    # Distance to Moon in km
    dist_km = (
        385000.56
        - 20905.355 * c(mp)
        - 3699.111 * c(2.0 * d - mp)
        - 2955.968 * c(2.0 * d)
        - 569.925 * c(2.0 * mp)
        + 246.158 * c(2.0 * d - 2.0 * mp)
        - 152.138 * c(2.0 * d - m - mp)
        - 170.733 * c(2.0 * d + mp)
        - 204.586 * c(2.0 * d - m)
    )

    moon_lon = _norm_deg(lp + sigma_l)
    moon_lat = sigma_b

    # Equatorial coordinates (Geocentric)
    ra_geo, dec_geo = ecliptic_to_equatorial(moon_lon, moon_lat, eps)

    # Geocentric Alt/Az
    alt_geo, az_geo = equatorial_to_horizontal(ra_geo, dec_geo, lat_deg, lst)

    # Topocentric parallax correction: Moon is nearby, parallax shifts altitude down
    # Horizontal parallax pi = asin(R_earth / distance)
    parallax_deg = math.asin(6378.137 / dist_km) * RAD_TO_DEG
    alt_topo = alt_geo - parallax_deg * math.cos(alt_geo * DEG_TO_RAD)

    # Atmospheric refraction for visible altitude
    refraction = atmospheric_refraction_deg(alt_topo)
    apparent_alt = alt_topo + refraction

    # Sun position for phase calculation
    sun_lon, _, _ = sun_ecliptic_pos(t)

    # Phase calculation
    # Elongation psi between Sun and Moon
    cos_psi = math.cos(moon_lat * DEG_TO_RAD) * math.cos((moon_lon - sun_lon) * DEG_TO_RAD)
    psi_deg = math.acos(max(-1.0, min(1.0, cos_psi))) * RAD_TO_DEG

    # Phase angle i
    # Approximate phase angle from geocentric elongation
    i_deg = 180.0 - psi_deg
    k_illum = (1.0 + math.cos(i_deg * DEG_TO_RAD)) / 2.0
    illum_pct = round(k_illum * 100.0, 1)

    # Waxing vs Waning: difference between Moon and Sun ecliptic longitude
    diff_lon = _norm_deg(moon_lon - sun_lon)
    is_waxing = 0.0 <= diff_lon < 180.0

    # Synodic month = 29.53058867 days
    synodic_cycle_days = 29.53058867
    moon_age_days = round((diff_lon / 360.0) * synodic_cycle_days, 1)

    # Phase categorization
    if k_illum < 0.02:
        phase_name = 'New Moon'
        phase_code = 'new_moon'
    elif k_illum > 0.98:
        phase_name = 'Full Moon'
        phase_code = 'full_moon'
    elif is_waxing:
        if k_illum < 0.48:
            phase_name = 'Waxing Crescent'
            phase_code = 'waxing_crescent'
        elif k_illum <= 0.52:
            phase_name = 'First Quarter'
            phase_code = 'first_quarter'
        else:
            phase_name = 'Waxing Gibbous'
            phase_code = 'waxing_gibbous'
    else:
        if k_illum > 0.52:
            phase_name = 'Waning Gibbous'
            phase_code = 'waning_gibbous'
        elif k_illum >= 0.48:
            phase_name = 'Last Quarter'
            phase_code = 'last_quarter'
        else:
            phase_name = 'Waning Crescent'
            phase_code = 'waning_crescent'

    # Supermoon indicator (perigee < 360,000 km near full/new moon)
    is_supermoon = dist_km < 360000.0 and (k_illum > 0.90 or k_illum < 0.10)

    constellation = get_constellation(ra_geo, dec_geo)

    return {
        'timestamp': ts,
        'altitude_deg': round(apparent_alt, 1),
        'azimuth_deg': round(az_geo, 1),
        'azimuth_cardinal': azimuth_to_cardinal(az_geo),
        'is_visible': apparent_alt > 0.0,
        'illumination_pct': illum_pct,
        'phase_name': phase_name,
        'phase_code': phase_code,
        'is_waxing': is_waxing,
        'age_days': moon_age_days,
        'distance_km': int(round(dist_km)),
        'is_supermoon': is_supermoon,
        'constellation': constellation,
        'ra_deg': round(ra_geo, 2),
        'dec_deg': round(dec_geo, 2),
    }


def compute_moon_times(ref_ts: float, lat_deg: float, lon_deg: float) -> Dict[str, Any]:
    """
    Calculate today's Moonrise, Moonset, and Meridian Transit times
    by scanning altitude over a 36-hour window around ref_ts.
    """
    # Sample every 10 minutes over [-12h, +24h]
    start_ts = ref_ts - 12 * 3600
    end_ts = ref_ts + 24 * 3600
    step_sec = 600  # 10 minutes

    times: List[float] = []
    alts: List[float] = []

    curr = start_ts
    while curr <= end_ts:
        m = moon_position_details(curr, lat_deg, lon_deg)
        times.append(curr)
        alts.append(m['altitude_deg'])
        curr += step_sec

    # Find crossings of altitude = -0.583° (geometric horizon with refraction)
    horizon_alt = -0.583
    rises: List[int] = []
    sets: List[int] = []

    for i in range(len(alts) - 1):
        a1, a2 = alts[i], alts[i + 1]
        t1, t2 = times[i], times[i + 1]
        # Moonrise: ascending across horizon
        if a1 < horizon_alt <= a2:
            frac = (horizon_alt - a1) / (a2 - a1)
            t_cross = int(round(t1 + frac * (t2 - t1)))
            rises.append(t_cross)
        # Moonset: descending across horizon
        elif a1 >= horizon_alt > a2:
            frac = (horizon_alt - a1) / (a2 - a1)
            t_cross = int(round(t1 + frac * (t2 - t1)))
            sets.append(t_cross)

    # Next / closest rise and set relative to ref_ts
    # Look for the rise/set for the current day cycle
    next_rise = next((r for r in rises if r >= ref_ts - 6 * 3600), rises[0] if rises else None)
    next_set = next((s for s in sets if s >= ref_ts - 6 * 3600), sets[0] if sets else None)

    # Peak transit (meridian crossing)
    # Find local maximum in altitude
    max_idx = max(range(len(alts)), key=lambda i: alts[i])
    transit_time = int(times[max_idx])
    transit_alt = round(alts[max_idx], 1)

    return {
        'moonrise': next_rise,
        'moonset': next_set,
        'transit_time': transit_time,
        'transit_altitude_deg': transit_alt,
    }


def compute_twilight_times(ref_ts: float, lat_deg: float, lon_deg: float) -> Dict[str, Any]:
    """
    Calculate sunset, civil, nautical, and astronomical twilight for tonight.
    """
    # Sample sun altitude around sunset of current day
    jd = julian_day(ref_ts)
    t = julian_centuries(jd)
    sun_lon, _, _ = sun_ecliptic_pos(t)
    eps = obliquity_deg(t)
    ra_sun, dec_sun = ecliptic_to_equatorial(sun_lon, 0.0, eps)

    # Hour angle calculation for zenith angles
    # Civil dusk: sun alt = -6°
    # Nautical dusk: sun alt = -12°
    # Astronomical dusk: sun alt = -18°
    def time_at_sun_alt(target_alt_deg: float, is_morning: bool) -> Optional[int]:
        lat_rad = lat_deg * DEG_TO_RAD
        dec_rad = dec_sun * DEG_TO_RAD
        target_rad = target_alt_deg * DEG_TO_RAD

        cos_h0 = (math.sin(target_rad) - math.sin(lat_rad) * math.sin(dec_rad)) / (
            math.cos(lat_rad) * math.cos(dec_rad) + 1e-12
        )
        if cos_h0 < -1.0 or cos_h0 > 1.0:
            return None  # Midnight sun or polar night
        h0_deg = math.acos(cos_h0) * RAD_TO_DEG
        # Local solar noon in GST: LST = RA_sun => GMST = RA_sun - lon
        noon_gmst = _norm_deg(ra_sun - lon_deg)
        # Convert GMST to approximate UT timestamp around ref_ts
        today_mid = (ref_ts // 86400) * 86400
        jd_mid = julian_day(today_mid)
        mid_gmst = gmst_deg(jd_mid)
        hours_to_noon = _norm_deg(noon_gmst - mid_gmst) / 15.0
        noon_ts = today_mid + hours_to_noon * 3600.0

        offset_sec = (h0_deg / 15.0) * 3600.0
        return int(round(noon_ts - offset_sec if is_morning else noon_ts + offset_sec))

    astro_dusk = time_at_sun_alt(-18.0, is_morning=False)
    astro_dawn = time_at_sun_alt(-18.0, is_morning=True)
    if astro_dawn and astro_dusk and astro_dawn < astro_dusk:
        astro_dawn += 86400

    dark_hours = round((astro_dawn - astro_dusk) / 3600.0, 1) if (astro_dawn and astro_dusk) else 0.0

    return {
        'civil_dusk': time_at_sun_alt(-6.0, is_morning=False),
        'nautical_dusk': time_at_sun_alt(-12.0, is_morning=False),
        'astronomical_dusk': astro_dusk,
        'astronomical_dawn': astro_dawn,
        'darkness_hours': max(0.0, dark_hours),
    }


def compute_visible_planets(ref_ts: float, lat_deg: float, lon_deg: float) -> List[Dict[str, Any]]:
    """
    Calculate topocentric visibility for 5 naked-eye planets (Mercury, Venus, Mars, Jupiter, Saturn).
    Uses low-precision Keplerian orbital elements (JPL/Meeus).
    """
    jd = julian_day(ref_ts)
    t = julian_centuries(jd)
    lst = local_sidereal_time_deg(jd, lon_deg)
    eps = obliquity_deg(t)

    # Sun position for elongation. Note: Earth seen from Sun is opposite Sun seen from Earth.
    sun_lon, _, r_sun = sun_ecliptic_pos(t)
    x_earth = -r_sun * math.cos(sun_lon * DEG_TO_RAD)
    y_earth = -r_sun * math.sin(sun_lon * DEG_TO_RAD)

    # Keplerian elements (a: AU, e, I: deg, L: deg, w: deg, node: deg)
    # V0 is standard V(1,0) absolute visual magnitude
    planets_data = [
        {
            'name': 'Venus',
            'a': 0.723332, 'e': 0.006773, 'I': 3.3946,
            'L': 181.9798 + 58517.8156 * t, 'w': 131.5637 + 0.0048 * t, 'node': 76.6799 - 0.2780 * t,
            'v0': -4.40,
        },
        {
            'name': 'Jupiter',
            'a': 5.204267, 'e': 0.048498, 'I': 1.3030,
            'L': 34.3515 + 3034.9057 * t, 'w': 14.3312 + 0.2155 * t, 'node': 100.4644 + 0.1767 * t,
            'v0': -9.40,
        },
        {
            'name': 'Saturn',
            'a': 9.582017, 'e': 0.055546, 'I': 2.4886,
            'L': 50.0774 + 1222.1138 * t, 'w': 92.8614 - 0.0862 * t, 'node': 113.6655 - 0.2567 * t,
            'v0': -8.88,
        },
        {
            'name': 'Mars',
            'a': 1.523679, 'e': 0.093405, 'I': 1.8497,
            'L': 355.4330 + 19140.2993 * t, 'w': 336.0602 + 0.4439 * t, 'node': 49.5581 - 0.2950 * t,
            'v0': -1.52,
        },
        {
            'name': 'Mercury',
            'a': 0.387098, 'e': 0.205630, 'I': 7.0050,
            'L': 252.2509 + 149472.6746 * t, 'w': 77.4561 + 0.1594 * t, 'node': 48.3309 - 0.1254 * t,
            'v0': -0.42,
        },
    ]

    results = []
    for p in planets_data:
        m = _norm_deg(p['L'] - p['w']) * DEG_TO_RAD
        # Solve Kepler's equation E - e*sin(E) = M
        e = p['e']
        ecc_anom = m
        for _ in range(5):
            ecc_anom -= (ecc_anom - e * math.sin(ecc_anom) - m) / (1.0 - e * math.cos(ecc_anom))

        # Heliocentric coordinates in orbital plane
        x_orb = p['a'] * (math.cos(ecc_anom) - e)
        y_orb = p['a'] * math.sqrt(1.0 - e * e) * math.sin(ecc_anom)

        # Rotate to ecliptic coordinates
        w_rad = p['w'] * DEG_TO_RAD
        node_rad = p['node'] * DEG_TO_RAD
        inc_rad = p['I'] * DEG_TO_RAD

        x_h = (math.cos(node_rad) * math.cos(w_rad) - math.sin(node_rad) * math.sin(w_rad) * math.cos(inc_rad)) * x_orb + \
              (-math.cos(node_rad) * math.sin(w_rad) - math.sin(node_rad) * math.cos(w_rad) * math.cos(inc_rad)) * y_orb
        y_h = (math.sin(node_rad) * math.cos(w_rad) + math.cos(node_rad) * math.sin(w_rad) * math.cos(inc_rad)) * x_orb + \
              (-math.sin(node_rad) * math.sin(w_rad) + math.cos(node_rad) * math.cos(w_rad) * math.cos(inc_rad)) * y_orb
        z_h = math.sin(w_rad) * math.sin(inc_rad) * x_orb + math.cos(w_rad) * math.sin(inc_rad) * y_orb

        # Geocentric coordinates
        x_g = x_h - x_earth
        y_g = y_h - y_earth
        z_g = z_h
        dist_au = math.sqrt(x_g * x_g + y_g * y_g + z_g * z_g)

        # Geocentric ecliptic lon, lat
        p_lon = _norm_deg(math.atan2(y_g, x_g) * RAD_TO_DEG)
        p_lat = math.asin(z_g / dist_au) * RAD_TO_DEG

        # Equatorial (RA, Dec)
        ra, dec = ecliptic_to_equatorial(p_lon, p_lat, eps)

        # Current topocentric Alt, Az
        alt, az = equatorial_to_horizontal(ra, dec, lat_deg, lst)

        # Elongation from Sun
        cos_elong = math.cos(p_lat * DEG_TO_RAD) * math.cos((p_lon - sun_lon) * DEG_TO_RAD)
        elong_deg = math.acos(max(-1.0, min(1.0, cos_elong))) * RAD_TO_DEG

        # Magnitude estimate based on standard V(1,0) formula
        r_helio = math.sqrt(x_h * x_h + y_h * y_h + z_h * z_h)
        mag = round(p['v0'] + 5.0 * math.log10(max(0.1, r_helio * dist_au)), 1)

        # Visibility assessment
        # A planet is visible if altitude > 5° during night or twilight, and elongation > 10°
        visible_tonight = elong_deg > 12.0
        cardinal = azimuth_to_cardinal(az)
        constellation = get_constellation(ra, dec)

        # Viewing window guidance
        if p['name'] == 'Venus':
            if p_lon > sun_lon:
                viewing_window = 'Evening sky in the West after sunset'
            else:
                viewing_window = 'Morning sky in the East before sunrise'
        elif p['name'] == 'Mercury':
            viewing_window = 'Low on the horizon during twilight'
        else:
            if alt > 15.0:
                viewing_window = f'Visible tonight high in {cardinal} ({round(alt)}° elevation)'
            elif alt > 0:
                viewing_window = f'Visible low in {cardinal} sky'
            else:
                viewing_window = f'Rises later tonight in {cardinal}'

        results.append({
            'name': p['name'],
            'magnitude': mag,
            'altitude_deg': round(alt, 1),
            'azimuth_deg': round(az, 1),
            'azimuth_cardinal': cardinal,
            'constellation': constellation,
            'elongation_deg': round(elong_deg, 1),
            'visible_tonight': visible_tonight,
            'is_above_horizon': alt > 0.0,
            'viewing_window': viewing_window,
        })

    # Sort so currently visible / prominent planets appear first
    results.sort(key=lambda p: (not p['visible_tonight'], -p['altitude_deg']))
    return results


def compute_meteor_showers(ref_ts: float, lat_deg: float) -> List[Dict[str, Any]]:
    """
    Returns upcoming meteor showers with personalized Moon interference ratings.
    """
    ref_dt = datetime.fromtimestamp(ref_ts, tz=timezone.utc)
    curr_year = ref_dt.year

    # Annual meteor shower database
    # (name, start_month, start_day, end_month, end_day, peak_month, peak_day, radiant_dec, zhr)
    showers = [
        ('Quadrantids', 12, 28, 1, 12, 1, 3, +49.0, 110),
        ('Lyrids', 4, 14, 4, 30, 4, 22, +34.0, 18),
        ('Eta Aquariids', 4, 19, 5, 28, 5, 5, -1.0, 50),
        ('Delta Aquariids', 7, 12, 8, 23, 7, 30, -16.0, 25),
        ('Perseids', 7, 17, 8, 24, 8, 12, +58.0, 100),
        ('Orionids', 10, 2, 11, 7, 10, 21, +16.0, 20),
        ('Leonids', 11, 6, 11, 30, 11, 17, +22.0, 15),
        ('Geminids', 12, 4, 12, 20, 12, 14, +33.0, 120),
        ('Ursids', 12, 17, 12, 26, 12, 22, +76.0, 10),
    ]

    events = []
    for name, sm, sd, em, ed, pm, pd, radiant_dec, zhr in showers:
        # Check if radiant is visible from this latitude (radiant peak alt > 5°)
        peak_alt = 90.0 - abs(lat_deg - radiant_dec)
        if peak_alt < 5.0:
            continue  # Radiant never rises high enough

        # Compute peak timestamp for current or upcoming cycle
        peak_dt = datetime(curr_year, pm, pd, 22, 0, tzinfo=timezone.utc)
        if peak_dt.timestamp() < ref_ts - 86400 * 2:
            peak_dt = datetime(curr_year + 1, pm, pd, 22, 0, tzinfo=timezone.utc)

        days_until = int(round((peak_dt.timestamp() - ref_ts) / 86400.0))

        # Only report showers within next 120 days or active right now
        if days_until < -2 or days_until > 120:
            continue

        # Evaluate Moon phase on the peak night!
        peak_moon = moon_position_details(peak_dt.timestamp(), lat_deg, 0.0)
        moon_illum = peak_moon['illumination_pct']

        # Moon interference rating
        if moon_illum < 25.0:
            rating = 'Excellent'
            desc = f'Moonless dark skies ({int(moon_illum)}% {peak_moon["phase_name"].lower()}); ideal viewing'
        elif moon_illum < 50.0:
            rating = 'Good'
            desc = f'Moderate moon interference ({int(moon_illum)}% illuminated); favorable after moonset'
        elif moon_illum < 75.0:
            rating = 'Fair'
            desc = f'Moon is {int(moon_illum)}% illuminated; faint meteors washed out'
        else:
            rating = 'Poor'
            desc = f'Bright {peak_moon["phase_name"].lower()} ({int(moon_illum)}%) will wash out faint meteors'

        events.append({
            'type': 'meteor_shower',
            'name': f'{name} Meteor Shower',
            'peak_date': peak_dt.strftime('%b %d'),
            'peak_timestamp': int(peak_dt.timestamp()),
            'days_until': days_until,
            'is_active': days_until <= 0 and days_until >= -5,
            'zhr': zhr,
            'dark_sky_rating': rating,
            'summary': desc,
        })

    events.sort(key=lambda x: x['days_until'])
    return events


def compute_upcoming_lunar_milestones(ref_ts: float) -> List[Dict[str, Any]]:
    """
    Find upcoming New Moon and Full Moon dates within the next 45 days.
    """
    milestones = []
    # Step forward day by day
    synodic = 29.53058867 * 86400.0
    # Sample every 6 hours over 35 days
    curr = ref_ts
    end = ref_ts + 35 * 86400
    prev_m = moon_position_details(curr, 0.0, 0.0)
    step = 21600  # 6 hours

    found_full = False
    found_new = False

    while curr < end and not (found_full and found_new):
        curr += step
        m = moon_position_details(curr, 0.0, 0.0)
        # Full moon: illumination peaks (> 98%)
        if not found_full and m['phase_code'] == 'full_moon':
            dt = datetime.fromtimestamp(curr, tz=timezone.utc)
            days = max(0, int(round((curr - ref_ts) / 86400.0)))
            milestones.append({
                'type': 'full_moon',
                'name': 'Full Moon' + (' (Supermoon)' if m['is_supermoon'] else ''),
                'date': dt.strftime('%b %d'),
                'timestamp': int(curr),
                'days_until': days,
                'is_supermoon': m['is_supermoon'],
                'summary': 'Supermoon - near perigee' if m['is_supermoon'] else 'Peak lunar illumination',
            })
            found_full = True

        # New moon: illumination troughs (< 2%)
        if not found_new and m['phase_code'] == 'new_moon':
            dt = datetime.fromtimestamp(curr, tz=timezone.utc)
            days = max(0, int(round((curr - ref_ts) / 86400.0)))
            milestones.append({
                'type': 'new_moon',
                'name': 'New Moon',
                'date': dt.strftime('%b %d'),
                'timestamp': int(curr),
                'days_until': days,
                'is_supermoon': False,
                'summary': 'Prime dark-sky stargazing window',
            })
            found_new = True

    milestones.sort(key=lambda x: x['days_until'])
    return milestones


def compute_stargazing_forecast(
    moon_info: Dict[str, Any],
    twilight_info: Dict[str, Any],
    cloud_cover: Optional[float] = None
) -> Dict[str, Any]:
    """
    Synthesize tonight's stargazing quality score (0-10) and narrative.
    """
    score = 5.0

    # Darkness hours factor
    dark_hrs = twilight_info.get('darkness_hours', 6.0)
    if dark_hrs > 7.0:
        score += 2.0
    elif dark_hrs > 4.0:
        score += 1.0

    # Moon factor
    illum = moon_info.get('illumination_pct', 50.0)
    alt = moon_info.get('altitude_deg', 0.0)
    if alt <= 0.0:
        # Moon is below horizon!
        score += 3.0
        moon_desc = 'Moon below horizon (dark sky)'
    elif illum < 25.0:
        score += 2.0
        moon_desc = f'{int(illum)}% thin crescent (minimal glare)'
    elif illum < 60.0:
        score += 0.5
        moon_desc = f'{int(illum)}% phase (moderate moonlight)'
    else:
        score -= 2.0
        moon_desc = f'{int(illum)}% bright moon (washes out deep sky)'

    # Cloud cover factor
    if cloud_cover is not None:
        if cloud_cover <= 15.0:
            score += 2.0
        elif cloud_cover <= 40.0:
            score += 0.5
        elif cloud_cover <= 70.0:
            score -= 1.5
        else:
            score -= 4.0

    final_score = max(1, min(10, int(round(score))))
    if final_score >= 8:
        rating = 'Excellent'
    elif final_score >= 6:
        rating = 'Good'
    elif final_score >= 4:
        rating = 'Fair'
    else:
        rating = 'Poor'

    return {
        'score': final_score,
        'rating': rating,
        'moon_interference': moon_desc,
        'cloud_cover_pct': cloud_cover,
        'dark_hours': dark_hrs,
    }


def get_astronomy_overview(
    lat_deg: float,
    lon_deg: float,
    timezone_str: str = 'UTC',
    cloud_cover: Optional[float] = None,
    ref_ts: Optional[float] = None
) -> Dict[str, Any]:
    """
    Top-level aggregator generating the complete astronomy payload for GET /api/astronomy.
    Strictly topocentric and relative; never exposes raw coordinates.
    """
    now = ref_ts if ref_ts is not None else time.time()

    # Moon telemetry
    moon = moon_position_details(now, lat_deg, lon_deg)
    times = compute_moon_times(now, lat_deg, lon_deg)
    moon.update(times)

    # Twilight & darkness
    twilight = compute_twilight_times(now, lat_deg, lon_deg)

    # Visible planets
    planets = compute_visible_planets(now, lat_deg, lon_deg)

    # Meteor showers
    meteor_showers = compute_meteor_showers(now, lat_deg)

    # Upcoming lunar milestones
    milestones = compute_upcoming_lunar_milestones(now)

    # Combined upcoming events
    all_events = meteor_showers + milestones
    all_events.sort(key=lambda x: x.get('days_until', 999))

    # Stargazing forecast
    stargazing = compute_stargazing_forecast(moon, twilight, cloud_cover)

    return {
        'available': True,
        'server_time': int(now),
        'moon': moon,
        'twilight': twilight,
        'planets': planets,
        'events': all_events[:5],  # top 5 upcoming events
        'stargazing': stargazing,
    }
