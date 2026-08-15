"""FastAPI main application for the Family Shopping Bot."""
import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, Request, HTTPException, status
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel

from config import get_config
from bot import get_bot
from sheets_service import get_sheets_service

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Global state for initialization
_init_task: asyncio.Task = None
_init_complete = False
_init_error: str = None


class WebhookUpdate(BaseModel):
    """Telegram webhook update model."""
    update_id: int
    message: Dict[str, Any] = None
    edited_message: Dict[str, Any] = None
    channel_post: Dict[str, Any] = None
    edited_channel_post: Dict[str, Any] = None
    inline_query: Dict[str, Any] = None
    chosen_inline_result: Dict[str, Any] = None
    callback_query: Dict[str, Any] = None
    shipping_query: Dict[str, Any] = None
    pre_checkout_query: Dict[str, Any] = None
    poll: Dict[str, Any] = None
    poll_answer: Dict[str, Any] = None
    my_chat_member: Dict[str, Any] = None
    chat_member: Dict[str, Any] = None
    chat_join_request: Dict[str, Any] = None


async def _initialize_background():
    """Background initialization task."""
    global _init_complete, _init_error
    try:
        logger.info("Starting background initialization...")
        config = get_config()

        # Initialize Google Sheets
        try:
            sheets = get_sheets_service()
            sheets.initialize()
            logger.info("Google Sheets initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Google Sheets: {e}")
            # Don't raise - allow bot to start for debugging

        # Initialize Telegram bot
        bot = get_bot()
        await bot.initialize()

        # Set webhook if URL is configured
        if config.telegram.webhook_url:
            try:
                await bot.set_webhook(config.telegram.webhook_url)
                logger.info(f"Webhook set to: {config.telegram.webhook_url}")
            except Exception as e:
                logger.error(f"Failed to set webhook: {e}")
        else:
            logger.info("No webhook URL configured - running in polling mode (development)")

        _init_complete = True
        logger.info("Background initialization complete")
    except Exception as e:
        _init_error = str(e)
        logger.error(f"Background initialization failed: {e}")
        _init_complete = True


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager - starts server immediately, initializes in background."""
    global _init_task
    # Startup - fire off background initialization but don't wait
    logger.info("Starting Family Shopping Bot (server ready immediately)...")
    _init_task = asyncio.create_task(_initialize_background())

    yield

    # Shutdown
    logger.info("Shutting down Family Shopping Bot...")
    if _init_task and not _init_task.done():
        _init_task.cancel()
        try:
            await _init_task
        except asyncio.CancelledError:
            pass
    bot = get_bot()
    await bot.shutdown()
    logger.info("Shutdown complete")


app = FastAPI(
    title="Family Shopping Bot",
    description="Telegram bot for shared family shopping list with Google Sheets storage",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health", response_class=PlainTextResponse)
async def health_check():
    """Health check endpoint for load balancers."""
    return "OK"


@app.get("/ready")
async def readiness_check():
    """Readiness check - verifies initialization is complete and dependencies are available."""
    global _init_complete, _init_error

    if not _init_complete:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "initializing",
                "message": "Background initialization in progress"
            }
        )

    if _init_error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "error",
                "error": _init_error
            }
        )

    config = get_config()

    # Check Google Sheets
    sheets_ok = False
    try:
        sheets = get_sheets_service()
        sheets._build_service()  # Test connection
        sheets_ok = True
    except Exception:
        pass

    # Check Telegram bot token
    bot_ok = bool(config.telegram.bot_token and config.telegram.bot_token != "YOUR_BOT_TOKEN_HERE")

    if sheets_ok and bot_ok:
        return {"status": "ready", "sheets": "ok", "telegram": "ok"}
    else:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "status": "not ready",
                "sheets": "ok" if sheets_ok else "error",
                "telegram": "ok" if bot_ok else "error"
            }
        )


@app.post("/webhook")
async def webhook(request: Request):
    """
    Telegram webhook endpoint.

    Receives updates from Telegram and processes them.
    """
    try:
        # Parse JSON body
        update_data = await request.json()

        # Validate it's a proper update
        if "update_id" not in update_data:
            logger.warning("Received invalid webhook payload (no update_id)")
            raise HTTPException(status_code=400, detail="Invalid update payload")

        # Process update
        bot = get_bot()
        await bot.process_update(update_data)

        return JSONResponse(content={"ok": True})

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error processing webhook: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/webhook")
async def webhook_info():
    """Get webhook info (for debugging)."""
    bot = get_bot()
    try:
        info = await bot.build_application().bot.get_webhook_info()
        return {
            "url": info.url,
            "has_custom_certificate": info.has_custom_certificate,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message,
            "max_connections": info.max_connections,
            "allowed_updates": info.allowed_updates
        }
    except Exception as e:
        logger.error(f"Error getting webhook info: {e}")
        raise HTTPException(status_code=500, detail="Failed to get webhook info")


@app.post("/webhook/set")
async def set_webhook(request: Request):
    """Manually set webhook URL (admin only in production)."""
    config = get_config()
    data = await request.json()
    url = data.get("url", config.telegram.webhook_url)

    if not url:
        raise HTTPException(status_code=400, detail="No webhook URL provided")

    bot = get_bot()
    await bot.set_webhook(url)

    # Update config
    config.telegram.webhook_url = url

    return {"ok": True, "webhook_url": url}


@app.post("/webhook/delete")
async def delete_webhook():
    """Delete webhook (switch to polling)."""
    bot = get_bot()
    await bot.delete_webhook()
    config = get_config()
    config.telegram.webhook_url = ""
    return {"ok": True, "message": "Webhook deleted, switched to polling mode"}


# Admin/API endpoints for managing the shopping list
@app.get("/api/items")
async def get_items(status_filter: str = None):
    """Get all shopping items, optionally filtered by status."""
    sheets = get_sheets_service()

    if status_filter and status_filter.lower() == "pending":
        items = sheets.get_pending_items()
    else:
        items = sheets.get_all_items()

    return {"items": items, "count": len(items)}


@app.get("/api/items/pending")
async def get_pending_items():
    """Get only pending shopping items."""
    sheets = get_sheets_service()
    items = sheets.get_pending_items()
    return {"items": items, "count": len(items)}


@app.patch("/api/items/{row}/status")
async def update_item_status(row: int, status: str):
    """Update the status of an item (e.g., mark as bought)."""
    if status not in ["pending", "bought", "cancelled"]:
        raise HTTPException(status_code=400, detail="Invalid status. Use: pending, bought, cancelled")

    sheets = get_sheets_service()
    try:
        sheets.update_status(row, status)
        return {"ok": True, "row": row, "status": status}
    except Exception as e:
        logger.error(f"Error updating item status: {e}")
        raise HTTPException(status_code=500, detail="Failed to update status")


@app.get("/")
async def root():
    """Root endpoint with basic info."""
    return {
        "name": "Family Shopping Bot",
        "version": "1.0.0",
        "description": "Telegram bot for shared family shopping list with Google Sheets storage",
        "endpoints": {
            "health": "/health",
            "ready": "/ready",
            "webhook": "/webhook",
            "webhook_info": "/webhook (GET)",
            "set_webhook": "/webhook/set (POST)",
            "delete_webhook": "/webhook/delete (POST)",
            "api_items": "/api/items",
            "api_pending": "/api/items/pending",
            "api_update_status": "/api/items/{row}/status (PATCH)"
        }
    }


if __name__ == "__main__":
    import uvicorn
    config = get_config()
    uvicorn.run(
        "main:app",
        host=config.app.host,
        port=config.app.port,
        reload=config.app.environment == "development"
    )