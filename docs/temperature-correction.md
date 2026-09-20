# Temperature and humidity interpretation

PurpleAir's map layer “Estimated Temperature” converts the device's raw operating temperature to an estimated ambient value. Comparing that layer to unconverted local JSON creates a systematic difference.

The PurpleAir map runtime inspected on September 20, 2026 uses:

- Estimated °F = `1.0227 × raw °F − 9.375`
- Estimated relative humidity = `min(100, 1.4498 × raw RH + 7.022)`

For example, a raw reading of 81°F becomes 73.4637°F, displayed as 73°F when rounded to a whole degree. The layer's explanatory text uses an intercept of 9.3755; its JavaScript uses 9.375. That 0.0005°F difference is immaterial at display precision. Indigo follows the runtime formula and additionally clamps humidity to a minimum of zero.

Indigo defaults to this estimated view. Raw and simple (−8°F / +4 percentage points) views are selectable for both current readings and historical charts. Schema version 2 records the ingestion conversion; raw history is preserved and chart conversions are calculated from those raw values. CSV includes stored derived values, raw values, and the conversion identifier.

The PM2.5 EPA correction continues to use **raw humidity**, irrespective of the chart view. Forecast comparisons use the selected environment view. Sensor heating, siting, differing observation times, averaging windows, and rounding can still produce differences; the conversion is an estimate, not a calibrated weather-station measurement.

Sources: [PurpleAir map runtime](https://map.purpleair.com/pa.map.3.2.5-9043a290.modern.js), [PurpleAir measurement explanation](https://community.purpleair.com/t/what-do-purpleair-sensors-measure-and-how-do-they-work/3499), [temperature and humidity analysis](https://static.purpleair.com/docs/PurpleAir-PA-II-Temperatures-and-RH-Lance-Wallace.pdf).
