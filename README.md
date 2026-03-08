# Discord Message Deleter

A Python GUI tool to bulk-delete your Discord messages. Uses your Discord data package (GDPR export) to find message IDs, and can also discover new messages directly from the Discord API

![Python](https://img.shields.io/badge/python-3.8+-blue)

## Why?

Discord doesn't provide a way to bulk-delete your own messages. This tool reads message IDs from your local data export and can also scan the Discord API to discover messages sent after the export, so you always have a complete picture of what to delete.

<img width="615" height="863" alt="image" src="https://github.com/user-attachments/assets/7ba7beb6-0772-489c-9887-4baf5996080a" />


## Features

- Loads channels and messages from your Discord data package
- Groups channels by category (DMs, servers, group DMs)
- Select individual channels or bulk select/deselect all
- Progress bar with live status updates
- Rate limit handling (respects Discord's 429 responses)
- Stop button to cancel mid-deletion
- Tracks deleted messages in `deleted.json` — resume where you left off
- **Refresh via API** — check which messages still exist on Discord and update tracking
- **Discover New** — scan Discord for messages sent after your data export
- Live log showing each deletion result (success, skipped, failed)

## Setup

### 1. Request your Discord data package

1. Open Discord and go to **Settings > Privacy & Safety**
2. Scroll down and click **Request all of my Data**
3. Wait for Discord to email you a download link (can take up to 30 days)
4. Download and extract the zip — you'll get a `package/` folder

### 2. Get your Discord auth token

1. Open Discord in your **browser** (not the desktop app)
2. Press `F12` to open Developer Tools
3. Go to the **Network** tab
4. Send a message in any channel (or do any action in Discord)
5. Click on any request to `discord.com` in the network log
6. Look in the **Request Headers** section for `Authorization`
7. Copy the value — that's your token

> **Warning:** Your auth token gives full access to your Discord account. Never share it with anyone. This tool only sends it directly to Discord's API to delete your messages.

### 3. Install and run

**Option A: Standalone exe (no Python needed)**

Download `DiscordDeleter.exe` from the [Releases](../../releases) page. Place it in a folder alongside your `package/` directory and run it.

**Option B: Run from source**

```bash
pip install requests
python discord_deleter.py
```

Your folder should look like this:

```
discord_deleter/
  DiscordDeleter.exe    (or discord_deleter.py)
  package/
    Messages/
    Servers/
    Account/
```

## Usage

1. Run the script — all your channels load automatically
2. Paste your auth token into the token field
3. Check the channels you want to delete messages from
4. Click **Start Deletion**
5. Wait for it to finish, or click **Stop** to cancel

## How it works

The tool reads these files from your data package:

| File | Purpose |
|------|---------|
| `Messages/index.json` | Channel ID to display name mapping |
| `Messages/c{id}/channel.json` | Channel type (DM, server, group) and server info |
| `Messages/c{id}/messages.json` | Your messages with IDs and timestamps |

For each selected channel, it sends `DELETE` requests to Discord's API with a 1.4 second delay between each request to avoid rate limits. If rate limited (HTTP 429), it waits the required time before retrying.

### Deletion tracking

Successfully deleted messages are saved to `deleted.json` alongside the script. On next launch, those messages are filtered out so you only see what's left to delete. This means you can stop and resume at any time without re-deleting messages.

To start fresh, just delete the `deleted.json` file.

### Refresh via API

If you deleted messages outside this tool (e.g. via a browser script), click **Refresh via API** to sync up. It checks each selected message against the Discord API — messages that return 404 are marked as deleted in the tracking file. This uses a 0.5s delay between checks since it's read-only.

### Discover New

Click **Discover New** to find messages sent after your data export. It scans your current Discord DMs, group DMs, and server channels via the API, and adds any new messages to the channel list. Discovered data is saved to `discovered.json` so it persists across restarts.

**Limitations:** Only finds channels you currently have access to — open DMs, active group DMs, and servers you're still in. Messages from closed DMs or servers you've left can only come from the data package.

### Log

The log panel shows real-time feedback for each message:
- **Deleted** — successfully removed from Discord
- **Skipped (403/404)** — message was already gone or inaccessible
- **FAILED** — deletion failed (with HTTP status code)
- **Rate limited** — waiting before retrying

### Data files

The tool creates these files next to the exe/script:

| File | Purpose |
|------|---------|
| `deleted.json` | Message IDs that have been deleted — prevents re-processing |
| `discovered.json` | Messages found via Discover New that aren't in the data package |

Delete either file to reset that tracking.

## Disclaimer

This tool uses a self-bot token which is against Discord's Terms of Service. Use at your own risk. This is intended for deleting your own messages from your own account.
