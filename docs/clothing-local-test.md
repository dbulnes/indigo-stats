# Local clothing recommendation trial

This trial uses **synthetic weather**, not your installed sensor or history. It
creates a uniquely named disposable Docker container and volume, disables
scheduled jobs, and binds only `127.0.0.1:8877`. It never targets Unraid or an
installed `/data` volume. Use local Docker Desktop, not a remote Docker context.

## Start on an Apple Silicon Mac

From the application repository root:

```sh
docker build --platform linux/arm64 -t indigo-stats:clothing-test .
bash deploy/clothing-local-test.sh
```

An optional port argument (for example `8878`) avoids a local port conflict.
The script prompts for a JevModel key with terminal echo disabled. Press Enter
for a **no-key, no-spend UI/setup check**. An already exported
`CLOTHING_JEVMODEL_API_KEY` is also accepted, including an explicitly empty one:

```sh
CLOTHING_JEVMODEL_API_KEY='' bash deploy/clothing-local-test.sh
```

Never paste a real key into a command, committed file, chat, screenshot, or log.
For an inherited key, use a trusted secret manager or hidden shell prompt.
The launcher passes only the environment variable **name** to Docker, not its
value in command arguments. The key stays in the server environment, not SQLite,
backups, or browser storage. Docker administrators can still inspect container
environment variables: do not share `docker inspect` output.

## Deliberate browser test

Open the printed localhost URL and the clothing recommendation controls.
Choose JevModel if needed. With an empty key, expect a missing-key/setup state.
With a supplied key, review the confirmation before deliberately clicking
**Generate**. This is the paid step; starting the container and refreshing
fixtures do not contact a model or fetch weather. No paid request is run by
either trial script.

The fixture supplies complete hourly forecasts and a current synthetic
PurpleAir observation using the application's normal storage/provenance
functions. Coordinates `0,0` are placeholders, not a real installation.
The timezone is `Etc/UTC`; the fixture supplies 48 hours of forecasts.
At 23:00 UTC, eligible periods roll to tomorrow's 06:00–11:00, 11:00–17:00,
and 17:00–23:00. From midnight through 05:59 UTC, they remain the upcoming
daytime periods of the current date, not the following date. During daytime,
completed periods are skipped. Fresh matching cached results can be reused
across midnight without additional provider calls.

## Refresh evidence

The launcher prints an exact command for its newly created container:

```sh
docker exec -i -u 99:100 YOUR_PRINTED_TRIAL_NAME python - < deploy/clothing-test-weather.py
```

Run that command from the repository root immediately before Generate.
Observation collection age must be at most 180 seconds and source age at most
600 seconds; forecasts expire after three hours. Refresh when evidence expires
or no longer covers the selected day. Changed weather summaries can invalidate
cached recommendations; a subsequent deliberate Generate may cost again.
Jobs remain disabled, so evidence does not refresh automatically.

The fixture refuses to run without the disposable marker and disabled jobs,
or against an unmarked populated database, changed installation settings, or
sensor readings without synthetic identity. Do not change location/sensor
settings in this trial; start a new trial instead.

## Finish

The container stays running after success. Save the exact cleanup command
printed by the launcher; it removes only that trial's container and disposable
volume. Startup failure automatically cleans up only resources created by that
invocation. Never substitute an installed container or appdata volume name.
To change the key or test a rebuilt image, clean up this trial and start a new
one. No release, push, deployment, or real sensor configuration is required.
