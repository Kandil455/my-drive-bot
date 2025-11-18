# Telegram Intake Bot for Drive Access

This bot verifies each member via Telegram, collects their phone number, team affiliation, and email, and then automatically shares the appropriate Google Drive folder with them so they immediately have access to the right space without uploading anything themselves. Admins can inspect each team, see how many members were added, and copy their email lists.

## Features
- Forces phone sharing to ensure every entry is tied to a verified Telegram account.
- Lets the user pick one of the configured teams (الفرقة الأولى/الثانية/الثالثة by default).
- Validates an email address and, upon success, grants that email viewer access to the team’s Drive folder.
- Stores all metadata in a SQLite database (`BOT_DB_PATH`, default `bot_data.db`) so you can track who has been added to each team.
- Admins (defined via `ADMIN_IDS`) run `/admin` to see totals per team and tap a team to receive its ordered email list ready for mass copying.

## Environment variables
| Name | Description | Example |
| --- | --- | --- |
| `BOT_TOKEN` | Telegram bot token from @BotFather. | `123:ABC` |
| `GOOGLE_CREDENTIALS_PATH` | Path to the service account JSON that has Drive access. | `/path/to/service.json` |
| `TEAM_FOLDER_MAP` | JSON map of team name to Drive folder ID that should be shared when a member joins. | `{"الفرقة الأولى":"1AbCdE","الفرقة الثانية":"2XyZ"}` |
| `DEFAULT_DRIVE_FOLDER` | Optional folder ID used when a team has no explicit entry. | `1ZyxW` |
| `GOOGLE_DELEGATED_USER` | (Optional) admin email if the service account must impersonate a domain user. | `admin@school.edu` |
| `ADMIN_IDS` | Comma-separated Telegram IDs that can run `/admin` and copy emails. | `123456,789012` |
| `TEAM_CHOICES` | Optional JSON array or semi-colon list of team names the user can choose. Defaults to `["الفرقة الأولى","الفرقة الثانية","الفرقة الثالثة"]`. | `"[\"قسم الإدارة\",\"قسم البيئة\"]"` |
| `BOT_DB_PATH` | Optional location for the SQLite file. | `/data/bot.db` |

> **Tip:** Share each Drive folder with the service account email (or keep it under a shared drive the service account can access) so sharing permissions can be granted.

## Running the bot
```bash
pip install -r requirements.txt
export BOT_TOKEN="..."
export GOOGLE_CREDENTIALS_PATH="..."
# configure other vars...
python bot.py
```

## Admin flow
- Send `/admin` to get counts of how many people belong to each team and how many were granted folder access.
- Tap the button that corresponds to a team to receive the stacked list of emails (select all + copy to use in Drive sharing or mailing lists).

## Deployment notes
- Run under a process manager (systemd, Docker, etc.) so it can recover from restarts and keep sharing working.
- Keep `GOOGLE_CREDENTIALS_PATH` secure; rotate it with care.
- Monitor `bot_data.db` in case you need to archive stale entries, especially if onboarding is high traffic.
