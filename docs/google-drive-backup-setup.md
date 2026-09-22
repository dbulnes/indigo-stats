# Google Drive Backup Setup Guide

Indigo Stats supports automated, off-server backups to Google Drive. This guide walks you through setting up a dedicated Google Cloud project, creating OAuth credentials, configuring the container, and linking your Google account.

---

## Architecture & Security Model

- **Daily local snapshots:** Indigo Stats creates a local SQLite snapshot daily. An independent hourly background job reconciles local snapshots with Google Drive.
- **Retention:** Retains the latest 30 completed snapshots in Google Drive, automatically pruning older copies.
- **Restricted scope (`drive.file`):** Indigo Stats requests only the `https://www.googleapis.com/auth/drive.file` scope. It **cannot view, edit, or delete any other files** in your Google Drive—it only has access to files and folders it creates itself.
- **Isolated token storage:** The OAuth refresh token is written to `/data/secrets/google-drive-token.json` with file mode `0600` (read/write for container user only). It is never stored in SQLite, never included in backups, and never exposed in API responses or logs.
- **Manifest integrity:** Each snapshot upload pushes the database file first and a SHA-256 manifest last. An upload is considered complete only after size and SHA-256 verification succeed.

---

## Step 1: Create a Google Cloud Project

1. Navigate to the [Google Cloud Console](https://console.cloud.google.com/).
2. In the top navigation bar, click the project selector dropdown and click **New Project**.
3. Enter a project name, such as `Indigo Stats`, and click **Create**.
4. Make sure your new project is selected in the top bar.

---

## Step 2: Enable the Google Drive API

1. In the Google Cloud Console, open the navigation menu (☰) and select **APIs & Services** → **Library**.
2. In the search box, type `Google Drive API` and press Enter.
3. Click **Google Drive API** from the search results.
4. Click **Enable**.

---

## Step 3: Configure the OAuth Consent Screen

1. In the left sidebar, navigate to **APIs & Services** → **OAuth consent screen**.
2. Select the **User Type**:
   - **External**: Choose this for personal `@gmail.com` accounts.
   - **Internal**: Available only if you have a Google Workspace organization and want to restrict access to users in your domain.
   - Click **Create**.
3. Enter the required **App information**:
   - **App name**: `Indigo Stats`
   - **User support email**: Your email address.
   - **Developer contact information**: Your email address.
   - Click **Save and Continue**.
4. Configure **Scopes**:
   - Click **Add or Remove Scopes**.
   - Search for or locate `../auth/drive.file` (*"See, edit, create, and delete only the specific Google Drive files you use with this app"*).
   - Check the box for `https://www.googleapis.com/auth/drive.file`.
   - Click **Update**, then click **Save and Continue**.
5. Configure **Test Users** (Required for "External" user type in Testing status):
   - Click **Add Users**.
   - Enter your personal Google email address (the account you will use to store backups).
   - Click **Save and Continue**, then review the summary and return to the dashboard.

---

## Step 4: Create OAuth 2.0 Credentials

1. In the left sidebar, navigate to **APIs & Services** → **Credentials**.
2. Click **+ Create Credentials** at the top and select **OAuth client ID**.
3. Set **Application type** to **Web application**.
4. Set **Name** to `Indigo Stats Web Client`.
5. Under **Authorized redirect URIs**, click **+ Add URI**.
   - **For local testing on your Mac/PC:**
     ```
     http://localhost:8765/api/backups/google/callback
     ```
     > [!IMPORTANT]
     > Google OAuth requires `localhost` for HTTP redirect URIs. Do not use `127.0.0.1`—Google's security checks and PKCE validation will reject numeric IP addresses over HTTP.
   - **For Unraid / Production servers with HTTPS:**
     ```
     https://<your-domain-or-tailnet-host>/api/backups/google/callback
     ```
     > [!NOTE]
     > Non-localhost redirect URIs must use `https://`. You can use a reverse proxy (Nginx, Traefik, Caddy), Cloudflare Tunnel, or a Tailscale HTTPS domain.
6. Click **Create**.
7. A dialog will appear with your **Client ID** and **Client Secret**. Copy both values securely.

---

## Step 5: Configure Container Environment Variables

Pass your OAuth credentials into your Indigo Stats container using environment variables (or Docker secrets files):

| Environment Variable | Description | Example |
| :--- | :--- | :--- |
| `BACKUP_GOOGLE_CLIENT_ID` | OAuth 2.0 Client ID | `123456789-abc.apps.googleusercontent.com` |
| `BACKUP_GOOGLE_CLIENT_SECRET` | OAuth 2.0 Client Secret | `GOCSPX-xxxxxxxxxxxxxxxx` |
| `BACKUP_GOOGLE_CALLBACK_URI` | Exact callback URI registered in Step 4 | `http://localhost:8765/api/backups/google/callback` |

Alternatively, use the file-based variables: `BACKUP_GOOGLE_CLIENT_ID_FILE`, `BACKUP_GOOGLE_CLIENT_SECRET_FILE`, and `BACKUP_GOOGLE_CALLBACK_URI_FILE`.

### Example: Docker Run
```sh
docker run -d \
  --name indigo-stats \
  --restart unless-stopped \
  -p 127.0.0.1:8765:8000 \
  -v indigo-stats-data:/data \
  -e TZ=America/Los_Angeles \
  -e BACKUP_GOOGLE_CLIENT_ID="123456789-abc.apps.googleusercontent.com" \
  -e BACKUP_GOOGLE_CLIENT_SECRET="GOCSPX-xxxxxxxxxxxxxxxx" \
  -e BACKUP_GOOGLE_CALLBACK_URI="http://localhost:8765/api/backups/google/callback" \
  ghcr.io/dbulnes/indigo-stats:latest
```

### Example: Docker Compose
```yaml
services:
  indigo-stats:
    image: ghcr.io/dbulnes/indigo-stats:latest
    container_name: indigo-stats
    restart: unless-stopped
    ports:
      - "127.0.0.1:8765:8000"
    volumes:
      - ./data:/data
    environment:
      - TZ=America/Los_Angeles
      - BACKUP_GOOGLE_CLIENT_ID=123456789-abc.apps.googleusercontent.com
      - BACKUP_GOOGLE_CLIENT_SECRET=GOCSPX-xxxxxxxxxxxxxxxx
      - BACKUP_GOOGLE_CALLBACK_URI=http://localhost:8765/api/backups/google/callback
```

---

## Step 6: Link Google Drive in Indigo Stats

1. Open Indigo Stats in your web browser.
2. Navigate to **System** → **Backup Destination**.
3. In the **Destination** dropdown, select **Google Drive**.
4. Click **Save destination**.
5. The Google Drive status will show:
   `OAuth setup: configured · account: not linked.`
6. Click the **Link Google Drive** button.
   - A centered popup window will open displaying Google's account selection and permission screen.
   - Choose your Google account.
   - If prompted with a *"Google hasn't verified this app"* warning (normal for self-hosted apps in testing mode), click **Advanced** → **Go to Indigo Stats (unsafe)**.
   - Review the requested permission (*"See, edit, create, and delete only the specific Google Drive files you use with this app"*) and click **Continue**.
7. Once granted, Google redirects to the callback handler:
   - The popup displays **"Google Drive linked"** and automatically closes.
   - The main Indigo Stats window instantly updates to `account: linked` and displays a success confirmation.

---

## Step 7: Test and Verify Backups

1. Click **Test connection**:
   - Indigo Stats connects to Google Drive, locates or creates a folder titled **`Indigo Stats Backups`**, and tags it with a private app property (`indigoStats=backups-v1`).
   - A success message will appear confirming readiness.
2. Click **Back up now**:
   - An asynchronous backup job will queue and start.
   - Status updates in the background. Once finished, **Last remote success** will reflect the latest backup timestamp.
3. Check your Google Drive:
   - Open [Google Drive](https://drive.google.com/).
   - You will find the folder **`Indigo Stats Backups`**.
   - Inside, you will see timestamped snapshot files (e.g., `20260921T120000Z.sqlite`) and their corresponding integrity manifests (`20260921T120000Z.sqlite.sha256`).

---

## Disaster Recovery from Google Drive

If your server or host storage suffers catastrophic failure:

1. Launch a new container with the same Google OAuth credentials (`BACKUP_GOOGLE_CLIENT_ID`, `BACKUP_GOOGLE_CLIENT_SECRET`, `BACKUP_GOOGLE_CALLBACK_URI`).
2. Link Google Drive from the UI as described in Step 6.
3. Use the management CLI inside the container to list and fetch snapshots:
   ```sh
   # List completed remote backups
   python -m backend.manage remote-list

   # Fetch and verify a specific snapshot
   python -m backend.manage remote-fetch 20260921T120000Z.sqlite
   ```
4. `remote-fetch` downloads the snapshot, verifies its SHA-256 checksum and SQLite schema integrity, and writes it to `/data/recovery/`. Live database files are never overwritten automatically.
5. Follow the recovery instructions in [`docs/operations.md`](operations.md) to place the verified snapshot as `/data/indigo.sqlite`.

---

## Troubleshooting

### `redirect_uri_mismatch`
- **Cause:** The URL in `BACKUP_GOOGLE_CALLBACK_URI` does not match the URI registered under **Authorized redirect URIs** in Google Cloud Console.
- **Fix:** Verify every character matches exactly (including `http` vs `https`, port number, and path `/api/backups/google/callback`).

### `Access blocked: Indigo Stats has not completed the Google verification process`
- **Cause:** Your OAuth Consent Screen is in "Testing" mode, and your Google account has not been added as a test user.
- **Fix:** In Google Cloud Console, go to **OAuth consent screen** → **Test users**, click **Add users**, and add your Google email address.

### Popup Blocked
- **Cause:** Some browser configurations block automated popup windows.
- **Fix:** Allow popups for your Indigo Stats domain in your browser's address bar. Indigo Stats also automatically falls back to direct navigation if popups are suppressed.

### Token Revocation / `relink required`
- **Cause:** If you revoke access in your Google Account Security settings or change your Google account password, Google invalidates the refresh token.
- **Fix:** In Indigo Stats, click **Unlink Google Drive**, then click **Link Google Drive** to authorize a fresh token.
