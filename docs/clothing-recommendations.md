# Clothing recommendations: setup and operation

## Manual consent and cost

Recommendations run only after an explicit manual request. Opening the dashboard, refreshing it, collection jobs, and forecast jobs do not trigger provider requests. Before requesting, review the selected provider and consent to sending weather context and possible API charges. Any client allowed to reach this app is effectively an administrator; keep it on a trusted LAN or private tailnet.

The initial integration uses **jevmodel.org**, an independent provider, at `https://jevmodel.org/v1/systemone` with model `jev-latest`. It is not TypeSafe, and its key is not interchangeable with a TypeSafe key. Provider selection and the recommendation cache are persistent SQLite state. A cached result is not a new provider judgment; check its timestamp and context before relying on it.

The app reports actual token usage returned by the provider. No measured live usage is available yet. The earlier **3,000–6,000 input tokens across three full-day calls** figure is only a prior estimate, not a measurement, quota, price, or spending guarantee. Actual usage depends on manual requests and provider behavior; review your provider's prices and billing. Missing usage is not evidence of zero cost.

## What the rankings mean

Recommendations use recent PurpleAir readings and existing weather forecasts. They do not predict weather or fetch a new forecast through Jev. Local periods are **06:00–11:00**, **11:00–17:00**, and **17:00–23:00** in the configured timezone; ended periods are skipped.

After upgrading from an image that did not record forecast provenance, wait for the next successful forecast refresh before generating clothing recommendations. An existing forecast batch alone is not trusted for this feature until the collector has recorded its provenance. Check forecast collection health if recommendations remain unavailable; refreshing the dashboard is not a forecast refresh and does not authorize a paid recommendation request.

The provenance must match the configured location and timezone; missing or mismatched provenance blocks provider spending. Configuration or weather changes can leave cached recommendations visible but marked stale until their period ends. They never trigger an automatic paid refresh. Once weather is ready, use **Generate** manually, respecting the five-minute retry cooldown.

Sensor context also requires provenance matching the current sensor source settings. Immediately after upgrading or changing sensor settings, recommendations use forecast-only context with a warning until a successful sensor sample establishes that provenance. To contribute local context, the collector sample must be no more than 180 seconds old and the sensor's own timestamp must parse and be no more than 600 seconds old. A recent collection time alone does not prove the device reading is fresh.

Each eligible period has eight judgments:

- Five **Choice** questions rank relative preference within bottoms, base top, midlayer, outerwear, and footwear.
- Three **Noul** questions estimate usefulness of an umbrella, beanie, and gloves.

Relative preference is not a proven probability of comfort. Accessory estimated usefulness is also a model judgment, not a comfort or protection guarantee. Assume ordinary errands/light outdoor walking and average temperature sensitivity. Base tops assume removable layers can be added; midlayers assume a comfortable base top and an uninsulated protective shell if needed; outerwear assumes a base top and ordinary light midlayer in cool weather, not a heavy insulating midlayer. Layers are judged independently and may not form a coherent outfit. Use personal comfort, activity, and current conditions to decide what to wear.

The dashboard shows one period at a time. Choose a remaining period tab, then open a clothing group to compare its alternatives. Weather sources and layer assumptions are in the methodology disclosure; provider selection is under **Setup / settings**. Tabs and disclosures never make provider requests. **Generate** still prepares all remaining periods, reusing matching cached results—not only the selected tab.

## API keys in the Unraid template

No new mounts or ports are needed. Provider keys are optional container environment variables, configured through masked Unraid template inputs:

| Provider | Unraid field | Container variable |
| --- | --- | --- |
| jevmodel.org | jevmodel.org API key | `CLOTHING_JEVMODEL_API_KEY` |
| TypeSafe | TypeSafe API key (Advanced View) | `CLOTHING_TYPESAFE_API_KEY` |

1. Open **Unraid → Docker → Indigo Stats → Edit**.
2. Enter the key in the masked field for your chosen provider. Leave unused provider fields blank. The two services use separate keys and billing accounts.
3. Click **Apply**. Unraid recreates the container with the updated environment; retain the existing `/data` mapping to preserve readings, settings, and cached results. For an existing installation whose saved template lacks the new field, add a masked Variable with the exact container variable above; it requires an image containing this feature.
4. Open Indigo Stats, select the provider, and review its configuration/weather status. Only click **Generate** when ready to send weather context and spend credits.

**Masking hides the value in the Unraid UI; it does not encrypt it.** Unraid stores the key in its saved container template, and administrators can inspect the Docker container environment. Protect host templates and their backups. Do not share template exports, Docker inspection output, screenshots, logs, or support bundles containing credentials.

The application reads keys only from its server environment. It never stores them in SQLite, accepts them through its own dashboard, or returns them from its APIs. There is no clothing key-file requirement or fallback. For non-Unraid installations, supply the same environment variables through your runtime's protected configuration rather than committing values or placing literal keys in shell history. The [local container trial](clothing-local-test.md) offers a hidden prompt.

A request sends only compact weather summaries and generic clothing questions, not coordinates, addresses, sensor identifiers, IPs, or raw payloads. Weather context still leaves your server. Provider credentials are sent only to their intended provider for authentication.

## Persistence, backups, and recovery

Keep the same `/data` mapping when recreating the container. SQLite retains provider selection, cached recommendations, and provider-reported usage. Application SQLite backups, including their off-server copies, **exclude environment credentials**. After a database-only restore onto a new host, supply the key again through the container template or runtime environment.

Host-level backups of Unraid templates or Docker configuration may include keys and need separate protection. Store a recovery copy in a password manager. Rotate a key through the provider account and update the container field; do not delete appdata to rotate credentials.
