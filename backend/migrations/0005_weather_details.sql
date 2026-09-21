BEGIN IMMEDIATE;
ALTER TABLE forecasts ADD COLUMN weather_code INTEGER;
ALTER TABLE forecasts ADD COLUMN wind_speed REAL;
ALTER TABLE forecasts ADD COLUMN apparent_temperature REAL;
ALTER TABLE forecasts ADD COLUMN cloud_cover REAL;
ALTER TABLE forecasts ADD COLUMN sunrise INTEGER;
ALTER TABLE forecasts ADD COLUMN sunset INTEGER;
ALTER TABLE forecasts ADD COLUMN temp_min REAL;
COMMIT;
