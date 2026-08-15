# Family Shopping Bot - Complete Setup Guide

This guide will walk you through setting up the Family Shopping Bot from scratch. The bot allows family members to send shopping items via Telegram, which are automatically stored in a shared Google Sheet.

## ��� What You'll Build

- A Telegram bot that accepts messages like "diapers 2" or "tomatoes"
- Automatic storage in Google Sheets with timestamps and user tracking
- Free hosting on Google Cloud Run (2M requests/month free tier)
- Secure access control via allowed user IDs

---

## ��� Prerequisites

- Google account
- Telegram account
- GitHub account (for free CI/CD deployment)
- Basic comfort with command line

---

## ��� Step 1: Create Telegram Bot

### 1.1 Talk to BotFather

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g., "Family Shopping Bot")
4. Choose a username ending in `bot` (e.g., `family_shopping_bot`)
5. **Save the bot token** - you'll need it later!

### 1.2 Get Your User ID

1. Search for **@userinfobot** on Telegram
2. Send any message
3. **Save your User ID** (a number like `123456789`)
4. Repeat for each family member who should have access

### 1.3 Optional: Set Bot Commands

Send `/setcommands` to BotFather, select your bot, then paste:

```
start - Start the bot and see instructions
help - Show help message
list - Show current shopping list
status - Show bot status
```

---

## ��� Step 2: Set Up Google Sheets

### 2.1 Create a Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Click "Select a project" → "New Project"
3. Name it (e.g., "family-shopping-bot")
4. **Save the Project ID** (e.g., `family-shopping-bot-12345`)

### 2.2 Enable APIs

1. In the Cloud Console, go to **APIs & Services** → **Library**
2. Search for and enable:
   - **Google Sheets API**
   - **Google Drive API** (needed for service account access)

### 2.3 Create Service Account

1. Go to **IAM & Admin** → **Service Accounts**
2. Click **Create Service Account**
3. Name: `family-shopping-bot`
4. Description: `Service account for family shopping bot`
5. Click **Create and Continue**
6. Skip role assignment (click Continue)
7. Click **Done**

### 2.4 Create Service Account Key

1. Click on the created service account
2. Go to **Keys** tab → **Add Key** → **Create new key**
3. Choose **JSON** format
4. **Save the downloaded JSON file** as `credentials.json`
5. ������ **Keep this file secure!** Never commit it to git.

### 2.5 Create the Spreadsheet

1. Go to [Google Sheets](https://sheets.google.com/)
2. Create a new blank spreadsheet
2. Name it "Family Shopping List"
3. **Copy the Spreadsheet ID** from the URL:
   ```
   https://docs.google.com/spreadsheets/d/SPREADSHEET_ID_HERE/edit
   ```
4. Share the spreadsheet with your service account email:
   - Click **Share** → Add the service account email (e.g., `family-shopping-bot@my-project.iam.gserviceaccount.com`)
   - Give **Editor** access
   - Uncheck "Notify people"
   - Click **Share**

---

## ����� Step 3: Deploy to Google Cloud Run (Free!)

### 3.1 Prepare GitHub Repository

1. Create a new GitHub repository (public or private)
2. Push this project to it:
   ```bash
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/family-shopping-bot.git
   git push -u origin main
   ```

### 3.2 Create Artifact Registry Repository

1. In Cloud Console, go to **Artifact Registry** → **Repositories**
2. Click **Create Repository**
3. Name: `family-bot-repo`
4. Format: `Docker`
5. Region: Choose one close to you (e.g., `us-central1`)
6. Click **Create**

### 3.3 Create Service Account for Cloud Run

1. Go to **IAM & Admin** → **Service Accounts**
2. Click **Create Service Account**
3. Name: `cloud-run-deployer`
4. Grant roles:
   - **Cloud Run Admin**
   - **Service Account User**
   - **Secret Manager Secret Accessor**
   - **Artifact Registry Writer**
5. Click **Done**
6. **Copy the service account email** (e.g., `cloud-run-deployer@my-project.iam.gserviceaccount.com`)

### 3.4 Add GitHub Secrets

Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**

Add these secrets:

| Secret Name | Value |
|-------------|-------|
| `GCP_PROJECT_ID` | Your Google Cloud Project ID |
| `GCP_REGION` | Your region (e.g., `us-central1`) |
| `GCP_SA_KEY` | **Entire content** of the service account JSON key from Step 2.4 |
| `GCP_RUN_SA_EMAIL` | Service account email from Step 3.3 |
| `TELEGRAM_BOT_TOKEN` | Bot token from Step 1.1 |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated user IDs (e.g., `123456789,987654321`) |
| `GOOGLE_SPREADSHEET_ID` | Spreadsheet ID from Step 2.5 |
| `GOOGLE_SHEET_NAME` | Sheet tab name (default: `Sheet1`) |

> **Note:** `TELEGRAM_WEBHOOK_URL` will be set automatically after first deployment.

### 3.5 Trigger First Deployment

1. Go to **Actions** tab in GitHub
2. Select "Deploy to Cloud Run" workflow
3. Click **Run workflow** → **Run workflow**
4. Wait for completion (2-3 minutes)

### 3.6 Verify Deployment

After deployment succeeds:
1. Check the workflow output for the **Service URL**
2. Visit `https://YOUR_SERVICE_URL/health` - should return "OK"
3. Visit `https://YOUR_SERVICE_URL/ready` - should return ready status
4. The webhook will be automatically set to `https://YOUR_SERVICE_URL/webhook`

---

## ��� Step 4: Local Development (Alternative)

If you prefer to run locally without Cloud Run:

### 4.1 Prerequisites

- Docker and Docker Compose installed
- ngrok account (free) for webhook tunneling

### 4.2 Configure Local Environment

1. Copy config template:
   ```bash
   cp config.yaml.example config.yaml
   ```

2. Edit `config.yaml` with your values:
   ```yaml
   telegram:
     bot_token: "YOUR_BOT_TOKEN"
     webhook_url: ""  # Will be filled by ngrok
     allowed_user_ids:
       - 123456789
       - 987654321

   google_sheets:
     credentials_file: "credentials.json"
     spreadsheet_id: "YOUR_SPREADSHEET_ID"
     sheet_name: "Sheet1"
   ```

3. Place `credentials.json` in a `credentials/` folder:
   ```bash
   mkdir credentials
   cp /path/to/your/credentials.json credentials/
   ```

### 4.3 Start with ngrok Tunnel

1. Get ngrok auth token from [ngrok dashboard](https://dashboard.ngrok.com/get-started/your-authtoken)
2. Create `.env` file:
   ```bash
   echo "NGROK_AUTHTOKEN=your_token_here" > .env
   echo "TELEGRAM_BOT_TOKEN=your_token" >> .env
   echo "TELEGRAM_ALLOWED_USER_IDS=123456789,987654321" >> .env
   echo "GOOGLE_SPREADSHEET_ID=your_sheet_id" >> .env
   ```

3. Start services:
   ```bash
   docker-compose --profile tunnel up
   ```

4. Get the ngrok URL:
   - Open http://localhost:4040
   - Copy the HTTPS URL (e.g., `https://abc123.ngrok-free.app`)

5. Update webhook:
   ```bash
   curl -X POST http://localhost:8080/webhook/set \
     -H "Content-Type: application/json" \
     -d "{\"url\": \"https://abc123.ngrok-free.app/webhook\"}"
   ```

---

## ��� Step 5: Test the Bot

### 5.1 Basic Test

1. Open Telegram and message your bot
2. Send `/start` - should see welcome message
3. Send `diapers 2` - should confirm addition
4. Send `tomatoes` - should confirm (quantity defaults to 1)
5. Send `milk 1L` - should confirm with unit

### 5.2 Verify Google Sheets

1. Open your Google Sheet
2. You should see headers: Timestamp, User Name, User ID, Item, Quantity, Status
3. Your test items should appear as new rows

### 5.3 Test Commands

- `/list` - Shows pending items
- `/status` - Shows bot statistics

---

## ���‍����‍����‍���� Step 6: Add Family Members

1. Get each person's Telegram User ID (using @userinfobot)
2. Add to GitHub secret `TELEGRAM_ALLOWED_USER_IDS` (comma-separated)
3. Re-run the deployment workflow
4. They can now message the bot!

---

## ��� Configuration Reference

### config.yaml

```yaml
telegram:
  bot_token: "BOT_TOKEN"           # Required
  webhook_url: "WEBHOOK_URL"       # Auto-set in production
  allowed_user_ids: [123, 456]     # Required

google_sheets:
  credentials_file: "credentials.json"
  spreadsheet_id: "SHEET_ID"       # Required
  sheet_name: "Sheet1"             # Default: Sheet1

app:
  host: "0.0.0.0"
  port: 8080
  environment: "production"        # or development
```

### Environment Variables (override config.yaml)

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather |
| `TELEGRAM_WEBHOOK_URL` | Full webhook URL |
| `TELEGRAM_ALLOWED_USER_IDS` | Comma-separated user IDs |
| `GOOGLE_CREDENTIALS_FILE` | Path to credentials JSON |
| `GOOGLE_SPREADSHEET_ID` | Google Sheets ID |
| `GOOGLE_SHEET_NAME` | Sheet tab name |
| `APP_HOST` | Server host (default: 0.0.0.0) |
| `APP_PORT` | Server port (default: 8080) |
| `APP_ENVIRONMENT` | development/production |

---

## ��� Message Format Guide

| User Message | Parsed Item | Parsed Quantity |
|--------------|-------------|-----------------|
| `diapers` | diapers | 1 |
| `diapers 2` | diapers | 2 |
| `tomatoes 5` | tomatoes | 5 |
| `milk 1L` | milk | 1L |
| `bread 500g` | bread | 500g |
| `eggs 12 pcs` | eggs | 12 pcs |

---

## ������ Troubleshooting

### Bot doesn't respond
1. Check Cloud Run logs: `gcloud run services logs read family-shopping-bot --region=us-central1`
2. Verify webhook is set: Visit `/webhook` endpoint
3. Check bot token is correct in secrets

### "Not authorized" error
1. Verify user ID is in `TELEGRAM_ALLOWED_USER_IDS`
2. Check no extra spaces in the comma-separated list
3. User ID must be integer (no quotes)

### Google Sheets error
1. Verify service account has Editor access to the spreadsheet
2. Check Sheets API and Drive API are enabled
3. Verify `GOOGLE_SPREADSHEET_ID` is correct
4. Check credentials.json is valid and not expired

### Deployment fails
1. Check GitHub Actions logs
2. Verify all secrets are set correctly
3. Ensure Artifact Registry repository exists
4. Check Cloud Run service account has required roles

---

## ��� Cost Summary

| Service | Free Tier | Your Cost |
|---------|-----------|-----------|
| Cloud Run | 2M requests, 360k vCPU-sec, 180k GiB-sec/month | $0 |
| Artifact Registry | 0.5 GB storage | $0 |
| Secret Manager | 6 secret versions | $0 |
| Google Sheets/Drive | Unlimited personal use | $0 |
| **Total** | | **$0/month** |

---

## ��� Security Notes

- �� Service account has minimal required permissions
- �� Secrets stored in GitHub Secrets / Secret Manager (not in code)
- �� Non-root Docker user
- �� Input validation on all user messages
- �� Webhook only processes updates from Telegram (verify with secret token in production)

---

## ��� Advanced Features (Future Enhancements)

- [ ] Mark items as "bought" via inline buttons
- [ ] Categories (groceries, baby, household, etc.)
- [ ] Shared list view via web dashboard
- [ ] Notifications when items added
- [ ] Recurring items (weekly milk, etc.)
- [ ] Multi-language support

---

## ��� Support

If you encounter issues:
1. Check the troubleshooting section above
2. Review Cloud Run logs
3. Check GitHub Actions deployment logs
4. Open an issue in the GitHub repository

---

**Happy Shopping!** �������‍����‍����‍����