BEGIN IMMEDIATE;
ALTER TABLE forecasts ADD COLUMN uv_index REAL;
ALTER TABLE forecasts ADD COLUMN precipitation_probability REAL;
COMMIT;
