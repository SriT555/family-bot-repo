# Family Shopping Bot - Project State Documentation

**Last Updated**: 2026-08-22  
**Branch**: main  
**Latest Revision**: family-bot-00038-lvg  
**Service URL**: https://family-bot-978690844773.us-central1.run.app

---

## 🎯 Project Overview

A **Telegram bot** for shared family shopping lists with **Google Sheets** storage, deployed on **Google Cloud Run**.

---

## ✅ Current Features (All Working)

### 1. **Item Entry & Parsing**
| Input Format | Example | Result |
|--------------|---------|--------|
| Single item | `milk 2L` | milk (2L, misc) |
| With store suffix | `salt /costco` | salt (1, costco) |
| Comma-separated + store | `rice 5kg, dal 1kg /indian` | rice 5kg (indian), dal 1kg (indian) |
| "and" separator + store | `bread 2 and butter 1 /costco` | bread 2 (costco), butter 1 (costco) |
| Space-separated + store | `apples 5 bananas 2 /costco` | apples 5 (costco), bananas 2 (costco) |

**Valid Stores**: `costco`, `indian`, `misc` (default)

### 2. **Monthly Division (Auto-Headers)**
- **Auto-inserts** `=== Month YYYY ===` row at start of each new month
- **Format**: Bold, blue background (`#3399E6`), merged across all 6 columns
- **No timestamp** on header rows (clean visual separation)
- **Detection**: Only inserts once per month; detects existing headers correctly

### 3. **Google Sheets Structure** (6 columns)
| Col | Header | Example |
|-----|--------|---------|
| A | Timestamp | `2026-08-22T22:58:33Z` |
| B | User Name | `@testuser` |
| C | Item | `salt` |
| D | Quantity | `1` |
| E | Store | `costco` |
| F | Status | `pending` / `bought on 2026-08-22` |

### 4. **Commands**
| Command | Function |
|---------|----------|
| `/start` | Welcome + usage guide |
| `/help` | Same as /start |
| `/list` | All pending items |
| `/list /costco` | Filter by store (costco/indian/misc) |
| `/status` | Bot statistics |
| `/bought <item>` | Mark specific item as bought |
| `/bought /costco` | Mark all costco items as bought |
| `/bought /indian` | Mark all indian items as bought |
| `/bought /misc` | Mark all misc items as bought |
| `/bought all` | Mark ALL pending items as bought |

### 5. **API Endpoints**
| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Health check (returns "OK") |
| `/ready` | GET | Readiness check (sheets + telegram) |
| `/webhook` | POST | Telegram webhook receiver |
| `/webhook` | GET | Webhook info |
| `/webhook/set` | POST | Set webhook URL |
| `/webhook/delete` | POST | Delete webhook (polling mode) |
| `/api/items` | GET | All items (excludes month headers) |
| `/api/items/pending` | GET | Only pending items |
| `/api/items/{row}/status` | PATCH | Update item status |

---

## 🔧 Technical Stack

- **Runtime**: Python 3.12 on Cloud Run
- **Framework**: FastAPI + python-telegram-bot v20
- **Storage**: Google Sheets API (service account)
- **Deploy**: `gcloud run deploy --source=.`
- **Container**: Multi-stage Dockerfile (python:3.12-slim)

---

## 📁 Key Files

```
family_bot/
├── main.py              # FastAPI app + webhook + lifespan
├── bot.py               # Telegram bot handlers + parsing logic
├── sheets_service.py    # Google Sheets operations + monthly headers
├── config.py            # Config loader (YAML)
├── config.yaml          # Runtime config (not in git)
├── config.yaml.example  # Template
├── Dockerfile           # Multi-stage build
├── cloudbuild.yaml      # Cloud Build config
├── requirements.txt     # Dependencies
├── .claude/
│   └── skills/          # 10 custom skills (copied from parent)
│       ├── ai-image-generation
│       ├── ai-music
│       ├── ai-video-generation
│       ├── cloudflare
│       ├── find-skills
│       ├── grill-me
│       ├── higgsfield-product-photoshoot
│       ├── kling-3-0
│       ├── mcp-builder
│       └── xlsx
└── PROJECT_STATE.md     # This file
```

---

## ⚠️ Known Issues / Legacy Data

**Corrupted rows in sheet (rows 3-10 approx.)**: 
- From earlier bug where month headers got timestamps (`=== 2026-08 ===` was interpreted as formula =ERROR)
- **These are historical** - cannot be fixed programmatically
- **Manual cleanup**: Delete empty rows with timestamps but no item data in Google Sheets UI
- **New data is clean**: All new month headers have no timestamps

---

## 🚀 Deployment Commands

```bash
# Deploy (from family_bot directory)
gcloud run deploy family-bot --source=. --region=us-central1 --allow-unauthenticated

# Update webhook after deploy
curl -X POST https://family-bot-978690844773.us-central1.run.app/webhook/set \
  -H "Content-Type: application/json" \
  -d '{"url": "https://family-bot-978690844773.us-central1.run.app/webhook"}'

# Check logs
gcloud run services logs read family-bot --region=us-central1 --limit=50

# Health checks
curl https://family-bot-978690844773.us-central1.run.app/health
curl https://family-bot-978690844773.us-central1.run.app/ready
```

---

## 🔐 Configuration (config.yaml - NOT in git)

```yaml
telegram:
  bot_token: "YOUR_BOT_TOKEN_FROM_BOTFATHER"
  webhook_url: "https://family-bot-978690844773.us-central1.run.app/webhook"
  allowed_user_ids:
    - 1783439018
    - 8841711464

google_sheets:
  credentials_file: "credentials.json"
  spreadsheet_id: "YOUR_SPREADSHEET_ID"
  sheet_name: "Sheet1"

app:
  host: "0.0.0.0"
  port: 8080
  environment: "production"
```

**Required secrets** (set via Cloud Run or Secret Manager):
- `credentials.json` - Google Service Account key
- `bot_token` - From @BotFather

---

## 🧪 Test Examples (via curl)

```bash
# Add single item with store
curl -X POST https://family-bot-978690844773.us-central1.run.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 1, "message": {"message_id": 1, "from": {"id": 1783439018, "is_bot": false, "first_name": "Test"}, "chat": {"id": 1783439018, "type": "private"}, "date": 1724356800, "text": "salt /costco"}}'

# Add multiple items
curl -X POST https://family-bot-978690844773.us-central1.run.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 2, "message": {"message_id": 2, "from": {"id": 1783439018}, "chat": {"id": 1783439018, "type": "private"}, "date": 1724356800, "text": "rice 5kg, dal 1kg /indian"}}'

# List costco items
curl -X POST https://family-bot-978690844773.us-central1.run.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 3, "message": {"message_id": 3, "from": {"id": 1783439018}, "chat": {"id": 1783439018, "type": "private"}, "date": 1724356800, "text": "/list /costco", "entities": [{"offset": 0, "length": 5, "type": "bot_command"}]}}'

# Mark all costco as bought
curl -X POST https://family-bot-978690844773.us-central1.run.app/webhook \
  -H "Content-Type: application/json" \
  -d '{"update_id": 4, "message": {"message_id": 4, "from": {"id": 1783439018}, "chat": {"id": 1783439018, "type": "private"}, "date": 1724356800, "text": "/bought /costco", "entities": [{"offset": 0, "length": 7, "type": "bot_command"}]}}'

# Get all items via API
curl https://family-bot-978690844773.us-central1.run.app/api/items
```

---

## 📋 Next Steps / Future Enhancements

1. **Monthly summary API** - `/api/summary?month=August 2026`
2. **Per-store budget tracking** - Alert when store spending exceeds threshold
3. **Export to CSV/PDF** - Monthly reports
4. **User-specific item ownership** - Track who added what per month
5. **Scheduled cleanup** - Auto-archive old months to separate sheets
6. **Interactive buttons** - Inline keyboards for /list, /bought

---

## 💡 Resume Context

After restart, run:
```bash
cd C:\Users\dshre\OneDrive\Documents\claude code\family_bot
```

Then you can:
- Run `/skills` to see all 12 skills (2 built-in + 10 custom)
- Continue development from this state
- Deploy with `gcloud run deploy family-bot --source=. --region=us-central1 --allow-unauthenticated`

**Current focus**: Monthly headers working cleanly, store feature complete, all commands functional.