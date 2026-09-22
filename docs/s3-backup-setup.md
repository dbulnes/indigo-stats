# S3 and S3-Compatible Backup Setup Guide

Indigo Stats supports automated, off-server backups to AWS S3 and any S3-compatible object storage provider (including Cloudflare R2, MinIO, Backblaze B2, and Wasabi).

This guide explains the security model, provides step-by-step setup instructions for AWS and alternative providers, demonstrates least-privilege IAM policies, and covers testing and disaster recovery.

---

## Architecture & Security Model

- **Local-first source:** Indigo Stats creates a local SQLite snapshot daily. An independent hourly background job reconciles local snapshots with your S3 bucket.
- **Retention:** Retains the latest 30 completed snapshots in the bucket, automatically pruning older copies.
- **Strict credential isolation:** Access keys and secrets are **never** stored in SQLite, never entered in the web UI, and never exposed via APIs or application logs. They are supplied exclusively through container environment variables or Docker secrets files (`_FILE`).
- **HTTPS enforcement:** Any custom S3 endpoint **must** use HTTPS. Insecure HTTP endpoints are rejected at configuration time.
- **Manifest-last verification:** Uploads push the database snapshot first, storing its SHA-256 hash in S3 object metadata (`x-amz-meta-sha256`). Indigo Stats verifies the uploaded size and checksum via a `head_object` call before publishing the `.manifest.json` file. A backup is only marked complete when the manifest and metadata verify.
- **Atomic pruning:** Pruning deletes only matched `.sqlite` and `.manifest.json` pairs created by Indigo Stats. Unrelated objects in your bucket are never touched.

---

## Step 1: Provider Setup & IAM Permissions

### Option A: AWS S3

#### 1. Create a Bucket
1. Open the [AWS S3 Console](https://s3.console.aws.amazon.com/).
2. Click **Create bucket**.
3. Choose a unique **Bucket name** (e.g., `my-homelab-indigo-backups`).
4. Select your preferred **AWS Region** (e.g., `us-west-2`).
5. Ensure **Block Public Access** is fully enabled.
6. Leave default encryption enabled (Amazon S3-managed keys / SSE-S3) and click **Create bucket**.

#### 2. Create a Least-Privilege IAM Policy
Create an IAM Policy with only the minimal permissions required for Indigo Stats to list, upload, verify, and prune backups under its prefix:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "IndigoStatsBucketAccess",
      "Effect": "Allow",
      "Action": [
        "s3:ListBucket",
        "s3:HeadBucket"
      ],
      "Resource": "arn:aws:s3:::my-homelab-indigo-backups",
      "Condition": {
        "StringLike": {
          "s3:prefix": [
            "indigo-stats/*",
            "indigo-stats"
          ]
        }
      }
    },
    {
      "Sid": "IndigoStatsObjectAccess",
      "Effect": "Allow",
      "Action": [
        "s3:GetObject",
        "s3:PutObject",
        "s3:DeleteObject"
      ],
      "Resource": "arn:aws:s3:::my-homelab-indigo-backups/indigo-stats/*"
    }
  ]
}
```

*(If you do not specify a prefix, change the object resource to `arn:aws:s3:::my-homelab-indigo-backups/*` and remove the prefix condition).*

#### 3. Create an IAM User and Access Keys
1. Open the [AWS IAM Console](https://console.aws.amazon.com/iam/).
2. Create a service user (e.g., `indigo-stats-backup-agent`).
3. Attach the policy created above.
4. Go to the user's **Security credentials** tab, click **Create access key**, and select **Application running outside AWS**.
5. Save the **Access Key ID** and **Secret Access Key**.

---

### Option B: Cloudflare R2

Cloudflare R2 provides S3-compatible object storage with **zero egress fees**, making it ideal for off-site homelab backups.

1. In the [Cloudflare Dashboard](https://dash.cloudflare.com/), navigate to **R2** → **Overview**.
2. Click **Create bucket** and enter a bucket name (e.g., `indigo-stats-backups`).
3. Under **R2** → **Manage R2 API Tokens**, click **Create API token**:
   - Permissions: **Object Read & Write**.
   - Specify bucket: Limit to your backup bucket.
   - Click **Create API Token**.
4. Note your credentials:
   - **Access Key ID**
   - **Secret Access Key**
   - **Endpoint**: `https://<account_id>.r2.cloudflarestorage.com`
   - **Region**: `auto`

---

### Option C: MinIO (Self-Hosted S3)

If you run MinIO in your homelab or secondary server:

1. Create a bucket in MinIO (e.g., `indigo-backups`).
2. Create a dedicated Service Account / Access Key in the MinIO Console.
3. **Endpoint**: `https://minio.your-domain.local:9000`
   > [!IMPORTANT]
   > Indigo Stats enforces HTTPS for all S3 endpoints. If using self-hosted MinIO, configure a valid TLS certificate (e.g., via Let's Encrypt or your internal CA) or reverse proxy.
4. **Region**: Leave blank or specify your MinIO server's configured region (e.g., `us-east-1`).

---

### Option D: Backblaze B2

1. In the [Backblaze Console](https://secure.backblaze.com/b2_buckets.htm), create a private bucket.
2. In **Application Keys**, click **Add a New Application Key**:
   - Restrict access to your bucket with **Read and Write** capabilities.
3. Note your credentials:
   - **keyID** (`BACKUP_S3_ACCESS_KEY_ID`)
   - **applicationKey** (`BACKUP_S3_SECRET_ACCESS_KEY`)
   - **Endpoint**: `https://s3.<region>.backblazeb2.com` (found in bucket details)
   - **Region**: `<region>` (e.g., `us-west-004`)

---

## Step 2: Configure Container Environment Variables

Supply your S3 credentials to the Indigo Stats container via environment variables:

| Environment Variable | Description |
| :--- | :--- |
| `BACKUP_S3_ACCESS_KEY_ID` | S3 Access Key ID (or `_FILE` suffix for Docker secrets) |
| `BACKUP_S3_SECRET_ACCESS_KEY` | S3 Secret Access Key (or `_FILE` suffix for Docker secrets) |
| `BACKUP_S3_SESSION_TOKEN` | *(Optional)* Session token for temporary credentials |
| `BACKUP_S3_KMS_KEY_ID` | *(Optional)* KMS Key ID or ARN when using `sse-kms` encryption |

### Docker Run Example
```sh
docker run -d \
  --name indigo-stats \
  --restart unless-stopped \
  -p 127.0.0.1:8765:8000 \
  -v indigo-stats-data:/data \
  -e TZ=America/Los_Angeles \
  -e BACKUP_S3_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE" \
  -e BACKUP_S3_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY" \
  ghcr.io/dbulnes/indigo-stats:latest
```

### Docker Compose Example
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
      - BACKUP_S3_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE
      - BACKUP_S3_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY
```

---

## Step 3: Configure Destination in Indigo Stats UI

1. Open Indigo Stats in your web browser.
2. Navigate to **System** → **Backup Destination**.
3. In the **Destination** dropdown, select **S3 / S3-compatible**.
4. Configure the destination parameters:
   - **Bucket**: Your bucket name (e.g., `my-homelab-indigo-backups`).
   - **Prefix**: *(Optional)* Key prefix under which backups are stored (e.g., `indigo-stats`). Defaults to `indigo-stats`.
   - **Region**: Your bucket's AWS region (e.g., `us-west-2`) or `auto` for Cloudflare R2.
   - **Custom HTTPS endpoint**: Leave blank for standard AWS S3. For Cloudflare R2, MinIO, or Backblaze B2, enter the full HTTPS URL (e.g., `https://<account_id>.r2.cloudflarestorage.com`).
   - **Server-side encryption**:
     - `Provider default`: Uses the bucket's default encryption configuration.
     - `SSE-S3`: Requests Amazon S3-managed encryption (`AES256`).
     - `SSE-KMS`: Requests AWS Key Management Service encryption (`aws:kms`).
5. Click **Save destination**.
   - Notice that stored values are redacted in the UI for security.
   - The status indicator will report: `Credentials: configured`.

---

## Step 4: Test and Verify

1. Click **Test connection**:
   - Indigo Stats sends a `head_bucket` probe to verify bucket existence and IAM permissions.
   - A green success message will confirm: `Backup destination updated.`
2. Click **Back up now**:
   - Queues an immediate asynchronous reconciliation run.
   - Status updates in the background. Once complete, **Last remote success** displays the latest backup timestamp.
3. Check your S3 Bucket:
   - In your S3 console or bucket browser, navigate to the configured prefix (e.g., `indigo-stats/`).
   - You will see timestamped snapshot files (e.g., `20260921T120000Z.sqlite`) and their corresponding manifests (`20260921T120000Z.sqlite.manifest.json`).

---

## Disaster Recovery from S3

If your server or local SSD fails completely, you can list and recover backups using the CLI inside a fresh container:

### Normal Recovery (Settings Restored)
```sh
# List all completed remote snapshots
python -m backend.manage remote-list

# Download and verify a specific snapshot
python -m backend.manage remote-fetch 20260921T120000Z.sqlite
```

### Total-Loss Recovery (Before Database Settings Exist)
If the local database was lost and container settings have not been restored, pass the S3 configuration object on standard input using `--config-stdin`:

```sh
printf '%s\n' '{"provider":"s3","bucket":"my-homelab-indigo-backups","prefix":"indigo-stats","region":"us-west-2"}' | \
  python -m backend.manage remote-list --config-stdin

printf '%s\n' '{"provider":"s3","bucket":"my-homelab-indigo-backups","prefix":"indigo-stats","region":"us-west-2"}' | \
  python -m backend.manage remote-fetch 20260921T120000Z.sqlite --config-stdin
```

`remote-fetch` downloads the snapshot, verifies its SHA-256 against the manifest, runs `PRAGMA integrity_check`, and writes the verified file to `/data/recovery/`. It refuses to overwrite existing files and never touches the live database.

Follow the restore procedure in [`docs/operations.md`](operations.md#restore) to place the verified database at `/data/indigo.sqlite`.

---

## Troubleshooting

### `AccessDenied` / `403 Forbidden`
- **Cause:** The credentials provided in `BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY` lack permissions on the bucket or prefix.
- **Fix:** Review your IAM policy. Ensure both `s3:ListBucket` and `s3:HeadBucket` are permitted on the bucket ARN, and `s3:GetObject`, `s3:PutObject`, and `s3:DeleteObject` are permitted on the prefix ARN.

### `Endpoint must use HTTPS`
- **Cause:** The custom endpoint URL begins with `http://`.
- **Fix:** Indigo Stats requires HTTPS for custom S3 endpoints to prevent plaintext transmission of database snapshots across networks. Update the endpoint to use `https://`.

### `S3 upload verification failed`
- **Cause:** Size or checksum mismatch between the uploaded object and the local snapshot.
- **Fix:** Check network reliability and proxy timeouts. Indigo Stats validates `ContentLength` and `x-amz-meta-sha256` via `head_object` immediately after upload; if a proxy or storage gateway alters the payload, verification will fail.

### Switching Destinations
- When you switch destinations or prefixes, Indigo Stats maintains a distinct destination fingerprint in the database. It will reconcile new backups without deleting or corrupting snapshots in your previous destination.
