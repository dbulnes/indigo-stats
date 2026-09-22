"""
Tests for astronomical ephemeris, topocentric calculations, and /api/astronomy endpoint.
"""
import os
import tempfile
import unittest
from datetime import datetime, timezone
from fastapi.testclient import TestClient

from backend import astronomy, db, config
from backend.app import app


class AstronomyUnitTests(unittest.TestCase):
    def test_julian_day_and_gmst(self):
        # 2000-01-01 12:00:00 UTC is epoch J2000.0 (JD 2451545.0)
        ts_j2000 = 946728000.0
        jd = astronomy.julian_day(ts_j2000)
        self.assertAlmostEqual(jd, 2451545.0, places=4)
        t = astronomy.julian_centuries(jd)
        self.assertAlmostEqual(t, 0.0, places=6)

        # GMST at J2000.0 is ~280.46° (18h 41m 50s)
        gmst = astronomy.gmst_deg(jd)
        self.assertAlmostEqual(gmst, 280.46, places=1)

    def test_coordinate_transforms(self):
        # Test ecliptic to equatorial conversion for vernal equinox (0°, 0°) -> (0°, 0°)
        ra, dec = astronomy.ecliptic_to_equatorial(0.0, 0.0, 23.44)
        self.assertAlmostEqual(ra, 0.0, places=2)
        self.assertAlmostEqual(dec, 0.0, places=2)

        # Horizontal coordinates: object at celestial pole (dec=90°) from lat=45° should have alt=45°
        alt, az = astronomy.equatorial_to_horizontal(0.0, 90.0, 45.0, 0.0)
        self.assertAlmostEqual(alt, 45.0, places=2)

    def test_moon_telemetry_bounds(self):
        # Reference timestamp: 2024-09-17 22:00:00 UTC (Super Harvest Moon in Pisces)
        ref_ts = 1726610400.0
        details = astronomy.moon_position_details(ref_ts, 37.77, -122.42)

        self.assertIn('illumination_pct', details)
        self.assertGreaterEqual(details['illumination_pct'], 95.0)  # Near Full Moon
        self.assertEqual(details['phase_name'], 'Full Moon')

        # Lunar distance in km should be strictly between perigee (~356,000 km) and apogee (~407,000 km)
        self.assertGreater(details['distance_km'], 350000)
        self.assertLess(details['distance_km'], 410000)

        # Altitude should be between -90 and +90
        self.assertGreaterEqual(details['altitude_deg'], -90.0)
        self.assertLessEqual(details['altitude_deg'], 90.0)

        # Azimuth should be between 0 and 360
        self.assertGreaterEqual(details['azimuth_deg'], 0.0)
        self.assertLess(details['azimuth_deg'], 360.0)

        # Constellation must be non-empty string
        self.assertIsInstance(details['constellation'], str)
        self.assertGreater(len(details['constellation']), 0)

    def test_moon_times(self):
        ref_ts = 1726963200.0  # 2024-09-22
        times = astronomy.compute_moon_times(ref_ts, 37.77, -122.42)
        self.assertIn('moonrise', times)
        self.assertIn('moonset', times)
        self.assertIn('transit_time', times)
        self.assertIn('transit_altitude_deg', times)

        # Transit altitude should be physical
        self.assertGreater(times['transit_altitude_deg'], 10.0)
        self.assertLessEqual(times['transit_altitude_deg'], 90.0)

    def test_visible_planets_elongation_limits(self):
        ref_ts = 1726963200.0
        planets = astronomy.compute_visible_planets(ref_ts, 37.77, -122.42)
        self.assertEqual(len(planets), 5)

        names = {p['name']: p for p in planets}
        self.assertIn('Mercury', names)
        self.assertIn('Venus', names)
        self.assertIn('Mars', names)
        self.assertIn('Jupiter', names)
        self.assertIn('Saturn', names)

        # Physical elongation limits:
        # Mercury maximum elongation from Sun is ~28°
        self.assertLessEqual(names['Mercury']['elongation_deg'], 29.0)
        # Venus maximum elongation is ~48°
        self.assertLessEqual(names['Venus']['elongation_deg'], 49.0)

        # Venus is typically very bright (magnitude < -3.0)
        self.assertLess(names['Venus']['magnitude'], -3.0)
        # Jupiter is bright (magnitude < -1.5)
        self.assertLess(names['Jupiter']['magnitude'], -1.5)

    def test_meteor_showers_and_moon_interference(self):
        ref_ts = 1726963200.0  # Late September
        showers = astronomy.compute_meteor_showers(ref_ts, 37.77)
        self.assertGreater(len(showers), 0)

        # Orionids (Oct 21) or Geminids (Dec 14) should be present
        shower_names = [s['name'] for s in showers]
        self.assertTrue(any('Orionids' in n or 'Geminids' in n or 'Leonids' in n for n in shower_names))

        # Each shower must have dark_sky_rating in {'Excellent', 'Good', 'Fair', 'Poor'}
        for s in showers:
            self.assertIn(s['dark_sky_rating'], {'Excellent', 'Good', 'Fair', 'Poor'})
            self.assertGreater(s['zhr'], 0)

    def test_stargazing_forecast(self):
        # Dark moonless night with clear skies
        moon_good = {'illumination_pct': 5.0, 'altitude_deg': -20.0}
        twilight_good = {'darkness_hours': 8.5}
        score_clear = astronomy.compute_stargazing_forecast(moon_good, twilight_good, cloud_cover=5.0)
        self.assertGreaterEqual(score_clear['score'], 8)
        self.assertEqual(score_clear['rating'], 'Excellent')

        # Full moon with overcast skies
        moon_bad = {'illumination_pct': 99.0, 'altitude_deg': 60.0}
        twilight_short = {'darkness_hours': 5.0}
        score_cloudy = astronomy.compute_stargazing_forecast(moon_bad, twilight_short, cloud_cover=90.0)
        self.assertLessEqual(score_cloudy['score'], 4)
        self.assertEqual(score_cloudy['rating'], 'Poor')


class AstronomyApiTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import patch
        from pathlib import Path
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(db, 'DATA', Path(self.tmp.name))
        self.patch.start()
        db.initialize()
        self.client = TestClient(app)

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_api_astronomy_unconfigured(self):
        # When location is not configured, /api/astronomy returns available=False gracefully
        res = self.client.get('/api/astronomy')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertFalse(data['available'])
        self.assertIn('reason', data)

    def test_api_astronomy_configured_and_privacy_guarantee(self):
        # Configure test coordinates (San Francisco)
        db.set_settings({
            'forecast_enabled': True,
            'latitude': 37.7749,
            'longitude': -122.4194,
            'timezone': 'America/Los_Angeles',
        })

        res = self.client.get('/api/astronomy')
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertTrue(data['available'])
        self.assertIn('moon', data)
        self.assertIn('planets', data)
        self.assertIn('events', data)
        self.assertIn('stargazing', data)

        moon = data['moon']
        self.assertIn('altitude_deg', moon)
        self.assertIn('azimuth_deg', moon)
        self.assertIn('illumination_pct', moon)
        self.assertIn('phase_name', moon)

        # STRICT PRIVACY INVARIANT (AGENTS.md):
        # Raw latitude, longitude, and street coordinates must NEVER appear anywhere in the response!
        data_str = res.text
        self.assertNotIn('37.7749', data_str)
        self.assertNotIn('-122.4194', data_str)
        self.assertNotIn('latitude', data)
        self.assertNotIn('longitude', data)


if __name__ == '__main__':
    unittest.main()
