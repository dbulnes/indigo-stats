import {
  Sun,
  Moon,
  Cloud,
  CloudSun,
  CloudMoon,
  CloudFog,
  CloudDrizzle,
  CloudRain,
  CloudSnow,
  CloudLightning,
  Snowflake,
  type LucideIcon,
} from 'lucide-react'

export type WeatherInfo = { icon: LucideIcon; label: string }

export function weatherCondition(code: number | null | undefined, isDay = true): WeatherInfo {
  if (code == null) return { icon: Cloud, label: 'Fair' }
  if (code === 0) return { icon: isDay ? Sun : Moon, label: 'Clear sky' }
  if (code <= 2) {
    return {
      icon: isDay ? CloudSun : CloudMoon,
      label: code === 1 ? 'Mainly clear' : 'Partly cloudy',
    }
  }
  if (code === 3) return { icon: Cloud, label: 'Overcast' }
  if (code <= 48) return { icon: CloudFog, label: 'Fog' }
  if (code <= 57) {
    return { icon: CloudDrizzle, label: code <= 55 ? 'Drizzle' : 'Freezing drizzle' }
  }
  if (code <= 67) {
    return {
      icon: CloudRain,
      label:
        code <= 61
          ? 'Light rain'
          : code <= 63
          ? 'Moderate rain'
          : code <= 65
          ? 'Heavy rain'
          : 'Freezing rain',
    }
  }
  if (code <= 77) return { icon: CloudSnow, label: 'Snow' }
  if (code <= 82) return { icon: CloudRain, label: 'Rain showers' }
  if (code <= 86) return { icon: Snowflake, label: 'Snow showers' }
  return { icon: CloudLightning, label: 'Thunderstorm' }
}

export function uvLabel(uv: number | null | undefined): string {
  if (uv == null) return '—'
  if (uv <= 2) return 'Low'
  if (uv <= 5) return 'Moderate'
  if (uv <= 7) return 'High'
  if (uv <= 10) return 'Very high'
  return 'Extreme'
}

/** Convert °F to °C */
export function toC(f: number | null | undefined): number | null {
  return f == null ? null : ((f - 32) * 5) / 9
}

/** Convert mph to km/h */
export function toKmh(mph: number | null | undefined): number | null {
  return mph == null ? null : mph * 1.60934
}
