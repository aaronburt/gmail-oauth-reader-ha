# Gmail OAuth Reader for Home Assistant

A Home Assistant custom integration that connects directly to the Gmail REST API via OAuth 2.0. It monitors your inbox, extracts delivery PINs and 2FA verification codes, and sequences incoming messages through a paced queue for Home Assistant automations.

---

## Features

- **2FA and Delivery Code Extraction**: Automatically extracts one-time passcodes and delivery PINs from couriers and services (Amazon, Iceland, DPD, Royal Mail, Google, Steam, banks).
- **Paced Ingestion Queue**: Processes incoming emails one at a time with a configurable dwell period to prevent overlapping notifications and automation conflicts.
- **Native Device Triggers**: Supports Home Assistant's visual Automation Editor with `new_email` and `new_otp` triggers.
- **Built-in Simulator**: Test automations, blueprints, and dashboards on demand via the `Simulate Test Email` button without waiting for real emails.
- **Native Repairs Flow**: Automatically detects expired OAuth tokens and provides a 1-click re-authentication prompt in Settings > System > Repairs.
- **Optional Outbound Email**: Can send outbound emails with attachments via the `gmail_oauth_reader.send_email` action when write permissions are granted.
- **Privacy First**: Direct communication between Home Assistant and Google's API. No intermediary cloud services or third-party proxies.

---

## Prerequisites

- Home Assistant 2024.11 or newer
- A Google Account
- A free Google Cloud project to obtain an OAuth Client ID and Secret

---

## Setup Guide

### Step 1: Create Google Cloud Credentials

Google requires OAuth credentials so Home Assistant can access your inbox.

1. Open the [Google Cloud Console](https://console.cloud.google.com/).
2. Create a new project named `Home Assistant Gmail`.
3. Select your project and navigate to **APIs & Services** > **Library**.
4. Search for **Gmail API** and click **Enable**.

#### Configure the OAuth Consent Screen
1. Navigate to **APIs & Services** > **OAuth consent screen**.
2. Select **External** (for `@gmail.com` accounts) and click **Create**.
3. Enter the required information:
   - **App name**: `Home Assistant Gmail`
   - **User support email**: Select your email address
   - **Developer contact information**: Enter your email address
4. Click **Save and Continue**.
5. On the **Scopes** page:
   - Click **Add or Remove Scopes**.
   - For read-only access (inbox monitoring, 2FA code extraction): add `https://www.googleapis.com/auth/gmail.readonly`
   - *(Optional)* If you plan to send emails or modify labels (mark read/archive) from Home Assistant: add `https://www.googleapis.com/auth/gmail.modify`
   - Click **Update**, then click **Save and Continue**.
6. On the **Test users** page:
   - Click **Add Users** and enter your Gmail address.
   - Click **Save and Continue**.

> [!NOTE]
> When a Google Cloud project is in "Testing" mode, Google requires personal accounts to be listed under **Test users**.

#### Generate OAuth Client ID
1. Navigate to **APIs & Services** > **Credentials**.
2. Click **Create Credentials** at the top and select **OAuth client ID**.
3. Set **Application type** to **Web application**.
4. Set **Name** to `Home Assistant`.
5. Under **Authorized redirect URIs**, click **Add URI** and enter:
   ```text
   https://my.home-assistant.io/redirect/oauth
   ```
   *(If you do not use My Home Assistant, enter `http://<YOUR_HA_IP>:8123/auth/external/callback` instead).*
6. Click **Create**.
7. Copy the generated **Client ID** and **Client Secret**.

---

### Step 2: Install the Integration

Copy the `custom_components/gmail_oauth_reader` directory into your Home Assistant `/config/custom_components/` folder:

```text
/config/
└── custom_components/
    └── gmail_oauth_reader/
```

Restart Home Assistant after copying the files.

---

### Step 3: Add the Integration in Home Assistant

1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. Click **Add Integration** in the bottom right corner.
3. Search for **Gmail OAuth Reader** and select it.
4. Paste the **Client ID** and **Client Secret** obtained in Step 1.
5. Follow the Google authentication prompt in your browser:
   - Select your Google account.
   - If prompted with "Google hasn't verified this app", click **Advanced** > **Go to Home Assistant Gmail (unsafe)**.
   - Click **Allow** to grant access.
6. The integration will complete setup and display your email address as the configured account.

---

## Testing Your Installation

To confirm the integration is working without waiting for inbound emails:

1. Navigate to **Settings** > **Devices & Services** > **Gmail OAuth Reader**.
2. Select your device (`Gmail (your_email@gmail.com)`).
3. Under the **Diagnostic** section, locate the **Simulate Test Email** button and click **Press**.
4. Verify that:
   - `sensor.gmail_latest_email` updates with a simulated message ID and attributes.
   - `sensor.gmail_latest_otp` displays a generated 6-digit verification code.

---

## Actionable Notification Blueprint

An automation blueprint is included at [`blueprints/automation/gmail_otp_actionable.yaml`](blueprints/automation/gmail_otp_actionable.yaml). It sends an actionable push notification to your mobile device whenever a 2FA code or delivery PIN arrives, with a button to copy the code directly to your clipboard.

### Usage

1. Copy `blueprints/automation/gmail_otp_actionable.yaml` into your `/config/blueprints/automation/` directory.
2. In Home Assistant, navigate to **Settings** > **Automations & Scenes** > **Blueprints**.
3. Select **Gmail 2FA / OTP Actionable Notification** and click **Create Automation**.
4. Choose your **OTP Sensor** (`sensor.gmail_latest_otp`) and target notify service.
5. Save the automation.

---

## Lovelace Dashboard Card

A pre-configured dashboard card is available at [`lovelace/gmail_inbox_card.yaml`](lovelace/gmail_inbox_card.yaml).

### Setup

1. Open your dashboard, click the menu in the top right, and select **Edit Dashboard**.
2. Click **Add Card** and choose **Manual** at the bottom of the card list.
3. Paste the contents of [`lovelace/gmail_inbox_card.yaml`](lovelace/gmail_inbox_card.yaml) into the editor.
4. Click **Save**.

The card displays:
- Unread count badge and queue status.
- A **Poll Now** button for immediate inbox synchronization.
- A dynamic 2FA banner that appears when a verification code is active.
- A list of recent emails with sender, timestamp, and body preview.

---

## Automation Examples

### Visual Automation Builder (UI Mode)

You can configure automations without YAML using Home Assistant's visual editor:

1. Go to **Settings** > **Automations & Scenes** > **Create Automation**.
2. Set the trigger to **Device** and select your **Gmail** device.
3. Choose either trigger:
   - `New email received`
   - `New 2FA / OTP verification code received`
4. Add desired actions (such as sending a notification or flashing a light).

### State Trigger Notification (YAML)

```yaml
alias: "Gmail: Inbound Email Alert"
trigger:
  - platform: state
    entity_id: sensor.gmail_latest_email
    not_to:
      - "idle"
      - "unknown"
      - "unavailable"
action:
  - action: notify.notify
    data:
      title: "New email from {{ state_attr('sensor.gmail_latest_email', 'sender_name') }}"
      message: "{{ state_attr('sensor.gmail_latest_email', 'subject') }}"
```

### Send Email with Attachment (YAML)

Requires write access enabled during initial setup or in the integration options.

```yaml
alias: "Security: Send Camera Snapshot"
trigger:
  - platform: state
    entity_id: binary_sensor.driveway_motion
    to: "on"
action:
  - action: camera.snapshot
    target:
      entity_id: camera.driveway
    data:
      filename: "/config/www/driveway_snapshot.jpg"
  - action: gmail_oauth_reader.send_email
    data:
      to: "recipient@example.com"
      subject: "Driveway Motion Alert"
      body: "Motion was detected. Snapshot attached."
      attachments:
        - "/config/www/driveway_snapshot.jpg"
```

---

## Configuration Options

To adjust options after installation, go to **Settings** > **Devices & Services** > **Gmail OAuth Reader** and click **Configure**.

| Option | Default | Description |
| :--- | :--- | :--- |
| **Polling interval** | `60` | Frequency in seconds between inbox queries (range: 30 to 600). |
| **Queue dwell time** | `5` | Seconds each email remains active in the sensor before advancing to the next queued message. |
| **Search query filter** | `is:unread label:INBOX` | Gmail query syntax used to filter retrieved messages. |
| **Extract 2FA / OTP codes** | `true` | Scans inbound emails for verification codes and delivery PINs. |
| **OTP code expiration** | `15` | Minutes a detected code remains in `sensor.gmail_latest_otp` before clearing to `'idle'`. |
| **Enable Write Access** | `false` | Grants `gmail.modify` scope for sending mail and modifying labels. Triggers an OAuth re-authentication when changed. Defaults to least-privilege `gmail.readonly`. |

---

## Security, Privacy & Recorder Optimization

### Least Privilege OAuth
By default, the integration requests only `gmail.readonly`, granting zero ability to send or alter emails if tokens were ever compromised. Outbound sending (`gmail_oauth_reader.send_email`) and label updates (`gmail_oauth_reader.modify_email`) require explicitly enabling **Enable Write Access**, which initiates a re-authentication flow requesting `gmail.modify`.

### Excluding Sensitive Sensors from Recorder
`sensor.gmail_latest_otp` holds transient authentication passcodes as its state, while `sensor.gmail_latest_email` provides recent email previews. To prevent storing sensitive one-time passcodes and high-frequency email previews in your Home Assistant database history, exclude them in your `configuration.yaml`:

```yaml
recorder:
  exclude:
    entities:
      - sensor.gmail_latest_otp
      - sensor.gmail_latest_email
```

Automations triggered by `gmail_oauth_reader_new_otp` or state triggers continue to work normally in real-time even when excluded from the long-term recorder database.

### Multi-Account Configurations
If multiple Gmail accounts are configured in Home Assistant, actions like `gmail_oauth_reader.send_email` or `gmail_oauth_reader.get_email_content` require the `entry_id` parameter to target the specific account:

```yaml
action: gmail_oauth_reader.send_email
data:
  entry_id: "your_config_entry_id_here"
  to: "recipient@example.com"
  subject: "Report"
  body: "Automated report."
```

---

## Troubleshooting

### Token Expiration and Repairs

Google Cloud projects in **Testing** status expire OAuth refresh tokens after 7 days. If a token expires:

1. Home Assistant will create an issue under **Settings** > **System** > **Repairs**.
2. Click **Submit** on the repair prompt to complete a one-click OAuth re-authentication.
3. Once authenticated, the repair issue resolves automatically and polling resumes.

To prevent 7-day expirations, set the OAuth consent screen publishing status to **In production** in the Google Cloud Console (verification is not required for private personal use).

### Diagnostics

To export debug data with sensitive information redacted, navigate to **Settings** > **Devices & Services** > **Gmail OAuth Reader**, click the three dots menu, and select **Download diagnostics**.

---

## Disclaimer

This project was built with AI assistance and reviewed by a human.
