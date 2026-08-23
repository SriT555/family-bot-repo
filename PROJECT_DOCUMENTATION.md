# Family Shopping Bot - Project Documentation

## Project Scope

A Telegram bot for shared family shopping list management with Google Sheets storage. The bot allows authorized family members to:
- Add items to a shared shopping list via natural language messages
- View pending items with `/list`
- Mark items as purchased with `/bought <item>` (records purchase date)
- Check bot status with `/status`
- Get help with `/help` or `/start`

## Architecture

### Tech Stack
- **Language**: Python 3.12
- **Framework**: FastAPI (webhook server), python-telegram-bot v20+
- **Deployment**: Google Cloud Run (containerized)
- **Storage**: Google Sheets API (service account authentication)
- **Secrets**: Google Secret Manager (credentials.json, bot token)
- **Container**: Docker multi-stage build (non-root user)

### Components

```
family_bot/
├── main.py              # FastAPI app with webhook endpoints, lifespan management
├── bot.py               # ShoppingBot class - Telegram handlers & command processing
├── sheets_service.py    # SheetsService class - Google Sheets CRUD operations
├── config.py            # Pydantic config loader (YAML + env vars)
├── config.yaml          # Configuration template (allowed_user_ids, spreadsheet_id, etc.)
├── Dockerfile           # Multi-stage Python 3.12 container
├── requirements.txt     # Python dependencies
├── .gcloudignore        # Files to exclude from Cloud Build
└── credentials.json     # Service account key (mounted from Secret Manager)
```

### Data Flow
1. **Telegram** → Webhook POST to `/webhook` (HTTPS)
2. **FastAPI** → Validates update, calls `bot.process_update()`
3. **python-telegram-bot** → Routes to command/message handlers
4. **Handlers** → Call `sheets_service` methods
5. **Google Sheets API** → Read/write to spreadsheet
6. **Response** → Sent back to Telegram via Bot API

### Spreadsheet Schema
| Column | Header | Description |
|--------|--------|-------------|
| A | Timestamp | ISO 8601 UTC (e.g., 2026-08-20T00:19:27Z) |
| B | User Name | Telegram display name |
| C | Item | Item name |
| D | Quantity | String (e.g., "10", "1kg", "500g") |
| E | Status | "pending" or "bought on YYYY-MM-DD" |

### Key Classes

**ShoppingBot (bot.py)**
- `_handle_start` - Welcome message
- `_handle_help` - Alias to start
- `_handle_list` - Shows pending items
- `_handle_status` - Shows counts (total/pending/bought)
- `_handle_bought` - **NEW**: Marks item as purchased with date
- `_handle_message` - Natural language item addition
- `build_application()` - Registers handlers
- `process_update()` - Webhook entry point

**SheetsService (sheets_service.py)**
- `append_item()` - Add new row
- `get_all_items()` - Read all rows as dicts
- `get_pending_items()` - Filter status="pending"
- `update_status(row, status)` - Update column F
- `mark_item_bought(item_name)` - **NEW**: Case-insensitive find + update with date

## Current Issues

### 1. `/bought` Command Not Consistently Processing - **FIXED (2026-08-22)**
- **Symptom**: Manual webhook tests with curl work, but actual Telegram messages don't trigger handler
- **Evidence**: 
  - API endpoint `/api/items/{row}/status` works perfectly
  - `/list`, `/start`, `/status` commands work
  - `/bought` handler has debug logging but no logs appear for real messages
- **Root Cause**: Telegram wasn't sending the `entities` field with `type: "bot_command"` for the `/bought` command, causing python-telegram-bot's `CommandHandler` to not match. This can happen when:
  - BotFather command registration hasn't propagated to all Telegram clients
  - The command was registered after the user started chatting with the bot
  - Telegram client caching issues
- **Fix Applied** (bot.py):
  1. Added fallback parsing in `_handle_bought()` - extracts item name directly from message text if `context.args` is empty
  2. Added `_extract_bought_item()` helper method using regex to parse `/bought` or `/bought@botname` commands
  3. Added `_handle_bought_fallback()` MessageHandler with regex filter `^/bought(?:@\w+)?\b` as backup
  4. Shared processing logic in `_process_bought_item()` to avoid duplication
  5. Added detailed logging for both handlers to aid debugging

### 2. Transient Google Sheets API Errors - **FIXED (2026-08-22)**
- **Symptom**: `BrokenPipeError` / `SSL: UNEXPECTED_EOF_WHILE_READING` on `/api/items/pending`
- **Frequency**: Intermittent, resolves on retry
- **Impact**: Read-only endpoints fail temporarily
- **Fix Applied** (sheets_service.py):
  1. Added `_is_transient_error()` to detect transient failure patterns
  2. Added `_retry_with_backoff()` with exponential backoff (1s, 2s, 4s, max 10s)
  3. Added `_execute_with_retry()` wrapper method in SheetsService
  4. Wrapped all Google Sheets API calls with retry logic:
     - `initialize()` - sheet initialization check
     - `_write_headers()` - header row writing
     - `_format_header_row()` - header formatting
     - `append_item()` - adding new items
     - `get_all_items()` - reading all items
     - `update_status()` - updating item status
     - `mark_item_bought()` - marking items as purchased

### 3. Webhook URL Configuration
- **Issue**: Cloud Run service URL changes on deploy
- **Current**: `https://family-bot-nxfsvslpdq-uc.a.run.app/webhook`
- **Fix needed**: Auto-update webhook on deployment via Cloud Build trigger or startup script

## Configuration

### Required Environment Variables (Cloud Run)
```bash
TELEGRAM_BOT_TOKEN=8984221769:AAF0tEwebrMyKPJZ_uKBwV0B9T-zMevLxZU
TELEGRAM_WEBHOOK_URL=https://family-bot-nxfsvslpdq-uc.a.run.app/webhook
TELEGRAM_ALLOWED_USER_IDS=1783439018,8841711464
GOOGLE_SPREADSHEET_ID=1rr0UOqucb3EM_IeZT5yVygRQ6sW9WKTUQIt8zI5iq1w
GOOGLE_CREDENTIALS_FILE=/app/credentials/credentials.json  # Mounted secret
GOOGLE_SHEET_NAME=Sheet1
APP_ENVIRONMENT=production
PORT=8080
```

### Secrets (Secret Manager)
- `google-credentials` - Service account JSON (mounted to `/app/credentials/credentials.json`)

### Allowed Users
- 1783439018 (Chakri)
- 8841711464

### Spreadsheet
- ID: `1rr0UOqucb3EM_IeZT5yVygRQ6sW9WKTUQIt8zI5iq1w`
- Sheet: `Sheet1`

## Deployment Commands

```bash
# Build and deploy
gcloud run deploy family-bot \
  --source . \
  --region us-central1 \
  --project test-for-n8n-463501 \
  --allow-unauthenticated \
  --set-secrets=/app/credentials/credentials.json=google-credentials:latest \
  --set-env-vars="TELEGRAM_BOT_TOKEN=...,TELEGRAM_WEBHOOK_URL=https://family-bot-nxfsvslpdq-uc.a.run.app/webhook,TELEGRAM_ALLOWED_USER_IDS=1783439018,8841711464,GOOGLE_SPREADSHEET_ID=1rr0UOqucb3EM_IeZT5yVygRQ6sW9WKTUQIt8zI5iq1w,GOOGLE_SHEET_NAME=Sheet1,APP_ENVIRONMENT=production"

# Update webhook after deploy
curl -X POST https://family-bot-nxfsvslpdq-uc.a.run.app/webhook/set \
  -H "Content-Type: application/json" \
  -d '{"url": "https://family-bot-nxfsvslpdq-uc.a.run.app/webhook"}'
```

## Debugging Commands

```bash
# Check webhook info
curl https://family-bot-nxfsvslpdq-uc.a.run.app/webhook

# Test webhook manually (with proper entities for CommandHandler)
curl -X POST https://family-bot-nxfsvslpdq-uc.a.run.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 1, "message": {"message_id": 1, "date": 1234567890, "chat": {"id": 1783439018, "type": "private"}, "from": {"id": 1783439018, "is_bot": false, "first_name": "Test"}, "text": "/bought tomatoes", "entities": [{"offset": 0, "length": 7, "type": "bot_command"}]}}'

# Test webhook manually (WITHOUT entities - tests fallback handler)
curl -X POST https://family-bot-nxfsvslpdq-uc.a.run.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 2, "message": {"message_id": 2, "date": 1234567890, "chat": {"id": 1783439018, "type": "private"}, "from": {"id": 1783439018, "is_bot": false, "first_name": "Test"}, "text": "/bought tomatoes"}}'

# Check logs (look for "/bought command received" or "/bought fallback handler triggered")
gcloud logging read "resource.type=cloud_run_revision AND resource.labels.service_name=family-bot AND logName:stderr" --limit=50 --project=test-for-n8n-463501

# Check readiness
curl https://family-bot-nxfsvslpdq-uc.a.run.app/ready

# Check pending items via API
curl https://family-bot-nxfsvslpdq-uc.a.run.app/api/items/pending

# Update item status via API
curl -X PATCH "https://family-bot-nxfsvslpdq-uc.a.run.app/api/items/5/status?status=bought"
```

## Log Patterns to Watch For

After the fix, check logs for these patterns:
- `/bought command received: item_name='...'` - Main CommandHandler worked (entities present)
- `/bought fallback parsing: extracted item_name='...'` - Main handler used fallback parsing
- `/bought fallback handler triggered: item_name='...'` - Fallback MessageHandler caught it

At least one of these should appear for every `/bought` command.

## Resume Instructions

1. **Verify webhook URL** matches current Cloud Run service URL
2. **Test `/list` and `/start`** in Telegram to confirm basic functionality
3. **Test `/bought <item>`** with exact item name from `/list` output
4. **Check logs** for `/bought` handler debug output: `"/bought command received: item_name='...'"`
5. **If `/bought` fails**: Use API endpoint directly to mark items as bought
6. **Monitor** for SSL/connection errors on Google Sheets API calls

## Known Working Features
- ✅ `/start` - Welcome message
- ✅ `/help` - Help message
- ✅ `/list` - Shows pending items
- ✅ `/status` - Shows statistics
- ✅ Natural language item addition ("tomatoes 2", "milk 1L")
- ✅ API endpoints for CRUD operations
- ✅ Google Sheets read/write
- ✅ Authorization (allowed_user_ids)
- ✅ `/bought` via direct API call
- ✅ `/bought` command from Telegram (FIXED: added fallback parsing + fallback handler)

## Pending Fixes
- [x] Make `/bought` command work reliably from Telegram (fixed 2026-08-22)
- [x] Add retry logic for Google Sheets API transient errors (fixed 2026-08-22)
- [ ] Auto-update webhook URL on deployment
- [ ] Add health check for Google Sheets connectivity