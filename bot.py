"""Telegram bot handler for the Family Shopping Bot."""
import logging
import re
from typing import Optional, Tuple
from dataclasses import dataclass

from telegram import Update, User
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from config import get_config
from sheets_service import get_sheets_service

logger = logging.getLogger(__name__)


@dataclass
class ParsedMessage:
    """Result of parsing a user message."""
    item: str
    quantity: str
    original_text: str


class ShoppingBot:
    """Telegram bot for family shopping list."""

    def __init__(self):
        self.config = get_config()
        self.sheets = get_sheets_service()
        self._application: Optional[Application] = None

    def _is_user_allowed(self, user_id: int) -> bool:
        """Check if user is in the allowed list."""
        allowed_ids = self.config.telegram.allowed_user_ids
        if not allowed_ids:
            logger.warning("No allowed user IDs configured - allowing all users")
            return True
        return user_id in allowed_ids

    def _parse_message(self, text: str) -> ParsedMessage:
        """
        Parse message text to extract item and quantity.

        Examples:
            "diapers 2" -> item="diapers", quantity="2"
            "tomatoes" -> item="tomatoes", quantity="1"
            "milk 1L" -> item="milk", quantity="1L"
            "bread  3" -> item="bread", quantity="3"
        """
        text = text.strip()
        if not text:
            return ParsedMessage(item="", quantity="1", original_text=text)

        # Split by whitespace
        parts = text.split()

        # Check if last part looks like a quantity (number, optionally with unit)
        quantity = "1"
        item = text

        if len(parts) > 1:
            last_part = parts[-1]
            # Match: number optionally followed by unit (kg, g, L, ml, pcs, etc.)
            qty_pattern = r"^(\d+(?:\.\d+)?)\s*(kg|g|mg|l|ml|pcs?|pieces?|packs?|boxes?|bottles?|cans?|jars?|bags?|units?|slices?)$"
            if re.match(qty_pattern, last_part, re.IGNORECASE):
                quantity = last_part
                item = " ".join(parts[:-1])
            # Also match just a number
            elif re.match(r"^\d+$", last_part):
                quantity = last_part
                item = " ".join(parts[:-1])

        return ParsedMessage(
            item=item.strip(),
            quantity=quantity,
            original_text=text
        )

    def _get_user_display_name(self, user: User) -> str:
        """Get a display name for the user."""
        if user.username:
            return f"@{user.username}"
        elif user.first_name:
            name = user.first_name
            if user.last_name:
                name += f" {user.last_name}"
            return name
        else:
            return f"User {user.id}"

    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle incoming text messages."""
        if not update.message or not update.message.text:
            return

        user = update.effective_user
        if not user:
            return

        user_id = user.id
        user_name = self._get_user_display_name(user)

        # Check authorization
        if not self._is_user_allowed(user_id):
            logger.warning(f"Unauthorized access attempt from user {user_id} ({user_name})")
            await update.message.reply_text(
                "��� Sorry, you're not authorized to use this bot. "
                "Please contact the admin to be added to the allowed list."
            )
            return

        # Parse message
        parsed = self._parse_message(update.message.text)

        if not parsed.item:
            await update.message.reply_text(
                "���� Please send an item name. Examples:\n"
                "• `diapers 2`\n"
                "• `tomatoes`\n"
                "• `milk 1L`\n"
                "• `bread 3`"
            )
            return

        # Add to Google Sheets
        try:
            self.sheets.append_item(
                user_name=user_name,
                user_id=user_id,
                item=parsed.item,
                quantity=parsed.quantity
            )

            # Send confirmation
            qty_text = f" ({parsed.quantity})" if parsed.quantity != "1" else ""
            await update.message.reply_text(
                f"��� Added to shopping list: **{parsed.item}**{qty_text}\n"
                f"���� Added by: {user_name}"
            )
            logger.info(f"Added item '{parsed.item}' for user {user_name}")

        except Exception as e:
            logger.error(f"Error adding item to sheets: {e}")
            await update.message.reply_text(
                "��� Failed to add item to shopping list. Please try again later."
            )

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        user_name = self._get_user_display_name(user)

        if not self._is_user_allowed(user.id):
            await update.message.reply_text(
                "��� You're not authorized to use this bot."
            )
            return

        await update.message.reply_text(
            f"���� Hi {user_name}! Welcome to the Family Shopping Bot.\n\n"
            "���� **How to use:**\n"
            "Just send me what you need to buy!\n\n"
            "**Examples:**\n"
            "• `diapers 2`\n"
            "• `tomatoes`\n"
            "• `milk 1L`\n"
            "• `bread 3`\n\n"
            "Items are automatically saved to our shared Google Sheet. "
            "Happy shopping! ���"
        )

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await self._handle_start(update, context)

    async def _handle_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list command - show pending items."""
        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            await update.message.reply_text("��� Not authorized.")
            return

        try:
            items = self.sheets.get_pending_items()

            if not items:
                await update.message.reply_text("���� Shopping list is empty!")
                return

            # Group by user for readability
            lines = ["���� **Current Shopping List:**\n"]
            for item in items:
                qty = f" x{item['quantity']}" if item['quantity'] != "1" else ""
                lines.append(f"• {item['item']}{qty} — *{item['user_name']}*")

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            logger.error(f"Error fetching list: {e}")
            await update.message.reply_text("��� Failed to fetch shopping list.")

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show bot status."""
        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            await update.message.reply_text("��� Not authorized.")
            return

        try:
            all_items = self.sheets.get_all_items()
            pending = [i for i in all_items if i['status'].lower() == 'pending']
            bought = [i for i in all_items if i['status'].lower() == 'bought']

            await update.message.reply_text(
                f"���� **Bot Status**\n"
                f"• Total items: {len(all_items)}\n"
                f"• Pending: {len(pending)}\n"
                f"• Bought: {len(bought)}\n"
                f"• Allowed users: {len(self.config.telegram.allowed_user_ids)}"
            )
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            await update.message.reply_text("��� Failed to get status.")

    def build_application(self) -> Application:
        """Build and configure the Telegram application."""
        if self._application is not None:
            return self._application

        # Create application
        self._application = (
            Application.builder()
            .token(self.config.telegram.bot_token)
            .build()
        )

        # Add handlers
        self._application.add_handler(CommandHandler("start", self._handle_start))
        self._application.add_handler(CommandHandler("help", self._handle_help))
        self._application.add_handler(CommandHandler("list", self._handle_list))
        self._application.add_handler(CommandHandler("status", self._handle_status))
        self._application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))

        # Error handler
        self._application.add_error_handler(self._error_handler)

        return self._application

    async def _error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE):
        """Handle errors in the bot."""
        logger.error(f"Exception while handling update: {context.error}")

    async def set_webhook(self, webhook_url: str):
        """Set the webhook URL for the bot."""
        app = self.build_application()
        await app.bot.set_webhook(url=webhook_url)
        logger.info(f"Webhook set to: {webhook_url}")

    async def delete_webhook(self):
        """Delete the webhook (switch to polling)."""
        app = self.build_application()
        await app.bot.delete_webhook()
        logger.info("Webhook deleted")

    async def initialize(self):
        """Initialize the bot application."""
        app = self.build_application()
        await app.initialize()
        logger.info("Bot application initialized")

    async def shutdown(self):
        """Shutdown the bot application."""
        if self._application:
            await self._application.shutdown()
            logger.info("Bot application shut down")

    async def process_update(self, update_data: dict):
        """Process a single update (for webhook mode)."""
        app = self.build_application()
        update = Update.de_json(update_data, app.bot)
        await app.process_update(update)


# Global bot instance
_bot: Optional[ShoppingBot] = None


def get_bot() -> ShoppingBot:
    """Get the global bot instance (singleton)."""
    global _bot
    if _bot is None:
        _bot = ShoppingBot()
    return _bot