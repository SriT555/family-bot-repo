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
from sheets_service import get_sheets_service, VALID_STORES

logger = logging.getLogger(__name__)

# Test hook trigger

# Simple user state for /bought flow (user_id -> dict with bought info)
_user_bought_state: dict[int, dict] = {}


@dataclass
class ParsedMessage:
    """Result of parsing a user message."""
    item: str
    quantity: str
    store: str
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

    def _parse_message(self, text: str) -> list[ParsedMessage]:
        """
        Parse message text to extract items, quantities, and store.
        Supports multiple items separated by comma, "and", or space (when store suffix present).

        Examples:
            "diapers 2" -> [ParsedMessage(item="diapers", quantity="2", store="misc")]
            "tomatoes" -> [ParsedMessage(item="tomatoes", quantity="1", store="misc")]
            "milk 1L, bread 2" -> [ParsedMessage(item="milk", quantity="1L", store="misc"), ParsedMessage(item="bread", quantity="2", store="misc")]
            "salt, pasta /costco" -> [ParsedMessage(item="salt", quantity="1", store="costco"), ParsedMessage(item="pasta", quantity="1", store="costco")]
            "salt pasta /costco" -> [ParsedMessage(item="salt", quantity="1", store="costco"), ParsedMessage(item="pasta", quantity="1", store="costco")]
            "rice 5kg dal 1kg /indian" -> [ParsedMessage(item="rice", quantity="5kg", store="indian"), ParsedMessage(item="dal", quantity="1kg", store="indian")]
        """
        text = text.strip()
        if not text:
            return [ParsedMessage(item="", quantity="1", store="misc", original_text=text)]

        # Extract store from /store suffix (e.g., "/costco", "/indian", "/misc")
        store = "misc"
        store_pattern = r"\s+/(" + "|".join(VALID_STORES) + r")$"
        store_match = re.search(store_pattern, text, re.IGNORECASE)
        if store_match:
            store = store_match.group(1).lower()
            text = text[:store_match.start()].strip()

        # Check if text has explicit separators (comma or "and")
        has_comma = ',' in text
        has_and = bool(re.search(r'\s+and\s+', text, re.IGNORECASE))

        # Split into item texts
        if has_comma or has_and:
            # Split by comma or "and"
            item_texts = re.split(r'\s*,\s*|\s+and\s+', text, flags=re.IGNORECASE)
        else:
            # No explicit separators: split intelligently by space if store was specified
            # Keep quantities with their items (e.g., "rice 5kg dal 1kg" -> ["rice 5kg", "dal 1kg"])
            if store != "misc":
                item_texts = self._split_space_separated_items(text)
            else:
                item_texts = [text]

        parsed_messages = []
        for item_text in item_texts:
            item_text = item_text.strip()
            if not item_text:
                continue

            # Split by whitespace
            parts = item_text.split()

            # Check if last part looks like a quantity (number, optionally with unit)
            quantity = "1"
            item = item_text

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

            parsed_messages.append(ParsedMessage(
                item=item.strip(),
                quantity=quantity,
                store=store,
                original_text=text
            ))

        return parsed_messages if parsed_messages else [ParsedMessage(item="", quantity="1", store="misc", original_text=text)]

    def _split_space_separated_items(self, text: str) -> list[str]:
        """
        Split space-separated items while keeping quantities with their items.

        Examples:
            "salt pasta" -> ["salt", "pasta"]
            "rice 5kg dal 1kg" -> ["rice 5kg", "dal 1kg"]
            "milk 1L bread 2" -> ["milk 1L", "bread 2"]
            "apples 10 bananas" -> ["apples 10", "bananas"]
            "tomatoes 5kg" -> ["tomatoes 5kg"]
            "milk" -> ["milk"]
        """
        parts = text.split()
        if len(parts) <= 1:
            return [text]

        # Regex to match quantity patterns (number with optional unit)
        qty_pattern = re.compile(
            r"^(\d+(?:\.\d+)?)\s*(kg|g|mg|l|ml|pcs?|pieces?|packs?|boxes?|bottles?|cans?|jars?|bags?|units?|slices?)$",
            re.IGNORECASE
        )

        result = []
        current_item_parts = []

        for i, part in enumerate(parts):
            # Check if this part is a quantity
            is_quantity = bool(qty_pattern.match(part) or re.match(r"^\d+$", part))

            if is_quantity:
                # Quantity belongs to the current item
                current_item_parts.append(part)
                result.append(" ".join(current_item_parts))
                current_item_parts = []
            else:
                # Not a quantity - if we have parts accumulated, finalize previous item
                if current_item_parts:
                    result.append(" ".join(current_item_parts))
                # Start new item with this part
                current_item_parts = [part]

        # Handle any remaining parts (last item without quantity)
        if current_item_parts:
            result.append(" ".join(current_item_parts))

        return result if result else [text]

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
                "🚫 Sorry, you're not authorized to use this bot. "
                "Please contact the admin to be added to the allowed list."
            )
            return

        # Parse message (returns list of ParsedMessage)
        parsed_list = self._parse_message(update.message.text)

        if not parsed_list or not parsed_list[0].item:
            await update.message.reply_text(
                "📝 Please send an item name. Examples:\n"
                "• `diapers 2`\n"
                "• `tomatoes`\n"
                "• `milk 1L`\n"
                "• `bread 3`\n"
                "• `salt, pasta /costco`\n"
                "• `rice 5kg and dal 1kg /indian`"
            )
            return

        # Add all items to Google Sheets
        added_items = []
        failed_items = []

        for parsed in parsed_list:
            try:
                self.sheets.append_item(
                    user_name=user_name,
                    item=parsed.item,
                    quantity=parsed.quantity,
                    store=parsed.store
                )
                added_items.append(parsed)
                logger.info(f"Added item '{parsed.item}' (store: {parsed.store}) for user {user_name}")

            except Exception as e:
                logger.error(f"Error adding item '{parsed.item}' to sheets: {e}")
                failed_items.append(parsed.item)

        # Send confirmation
        if added_items:
            lines = ["✅ Added to shopping list:"]
            for parsed in added_items:
                qty_text = f" ({parsed.quantity})" if parsed.quantity != "1" else ""
                store_text = f" [/{parsed.store}]" if parsed.store != "misc" else ""
                lines.append(f"• **{parsed.item}**{qty_text}{store_text}")

            if failed_items:
                lines.append(f"\n⚠️ Failed: {', '.join(failed_items)}")

            lines.append(f"\n👤 Added by: {user_name}")
            await update.message.reply_text("\n".join(lines))
        else:
            await update.message.reply_text(
                "⚠️ Failed to add items to shopping list. Please try again later."
            )

    async def _handle_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command."""
        user = update.effective_user
        user_name = self._get_user_display_name(user)

        if not self._is_user_allowed(user.id):
            await update.message.reply_text(
                "🚫 You're not authorized to use this bot."
            )
            return

        await update.message.reply_text(
            f"🛒 Hi {user_name}! Welcome to the Family Shopping Bot.\n\n"
            "📋 **How to use:**\n"
            "Just send me what you need to buy!\n\n"
            "**Add items:**\n"
            "• `diapers 2`\n"
            "• `tomatoes`\n"
            "• `milk 1L`\n"
            "• `bread 3`\n"
            "• `salt /costco` (store: costco, indian, misc)\n"
            "• `rice 5kg /indian`\n"
            "• `salt, pasta /costco` (multiple items)\n"
            "• `salt pasta /costco` (space-separated with store)\n\n"
            "**List items:**\n"
            "• `/list` - all pending items\n"
            "• `/list /costco` - only costco items\n"
            "• `/list /indian` - only indian items\n"
            "• `/list /misc` - only misc items\n\n"
            "Stores: `/costco` `/indian` `/misc` (default: misc)\n\n"
            "Items are automatically saved to our shared Google Sheet. "
            "Happy shopping! 🛍️"
        )

    async def _handle_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command."""
        await self._handle_start(update, context)

    async def _handle_list(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list command - show pending items (optionally filtered by store).

        Usage:
            /list - show all pending items
            /list /costco - show only costco items
            /list costco - show only costco items
            /list /indian - show only indian items
            /list /misc - show only misc items
        """
        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            await update.message.reply_text("🚫 Not authorized.")
            return

        # Check for store filter in command arguments
        store_filter = None
        if context.args:
            arg = context.args[0].lower().lstrip('/')
            if arg in VALID_STORES:
                store_filter = arg

        try:
            items = self.sheets.get_pending_items()

            if store_filter:
                items = [item for item in items if item.get('store', 'misc').lower() == store_filter]

            if not items:
                filter_text = f" for store '{store_filter}'" if store_filter else ""
                await update.message.reply_text(f"🛒 Shopping list is empty{filter_text}!")
                return

            # Group by user for readability
            filter_text = f" (filtered: /{store_filter})" if store_filter else ""
            lines = [f"🛒 **Current Shopping List{filter_text}:**\n"]
            for item in items:
                qty = f" x{item['quantity']}" if item['quantity'] != "1" else ""
                store = f" [/{item.get('store', 'misc')}]" if item.get('store', 'misc') != "misc" else ""
                lines.append(f"• {item['item']}{qty}{store} — *{item['user_name']}*")

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            logger.error(f"Error fetching list: {e}")
            await update.message.reply_text("⚠️ Failed to fetch shopping list.")

    async def _handle_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show bot status."""
        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            await update.message.reply_text("🚫 Not authorized.")
            return

        try:
            all_items = self.sheets.get_all_items()
            pending = [i for i in all_items if i['status'].lower() == 'pending']
            bought = [i for i in all_items if i['status'].startswith('✅')]

            await update.message.reply_text(
                f"📊 **Bot Status**\n"
                f"• Total items: {len(all_items)}\n"
                f"• Pending: {len(pending)}\n"
                f"• Bought: {len(bought)}\n"
                f"• Allowed users: {len(self.config.telegram.allowed_user_ids)}"
            )
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            await update.message.reply_text("⚠️ Failed to get status.")

    async def _handle_spending(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /spending command - show monthly spending summary."""
        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            await update.message.reply_text("🚫 Not authorized.")
            return

        # Optional month argument (e.g., "/spending August 2026")
        month_key = " ".join(context.args) if context.args else None

        try:
            summary = self.sheets.get_spending_summary(month_key)

            if summary['items_count'] == 0:
                month_text = f" for {summary['month']}" if summary['month'] else ""
                await update.message.reply_text(f"💰 No spending recorded{month_text} yet.")
                return

            lines = [f"💰 **Spending Summary — {summary['month']}**\n"]
            lines.append(f"📊 Total: **{summary['total']}**")
            lines.append(f"📦 Items bought: {summary['items_count']}\n")

            lines.append("🏪 **By Store:**")
            for store in ["costco", "indian", "misc"]:
                amount = summary['by_store'].get(store, 0)
                if amount > 0:
                    lines.append(f"• /{store.capitalize()}: **{amount}**")

            # Show recent items
            if summary['items']:
                lines.append("\n📋 **Recent purchases:**")
                for item in summary['items'][-10:]:  # Last 10 items
                    amt = item.get('amount', '')
                    if amt:
                        lines.append(f"• {item['item']} x{item['quantity']} [/{item.get('store', 'misc')}] — **{amt}**")

            await update.message.reply_text("\n".join(lines))

        except Exception as e:
            logger.error(f"Error getting spending summary: {e}")
            await update.message.reply_text("⚠️ Failed to get spending summary.")

    async def _handle_bought(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /bought command - ask for amount to mark item(s) as purchased."""
        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            await update.message.reply_text("🚫 Not authorized.")
            return

        # Get item name from command arguments
        item_name = " ".join(context.args) if context.args else ""

        # Fallback: parse from message text if entities not provided
        if not item_name and update.message and update.message.text:
            item_name = self._extract_bought_item(update.message.text)
            if item_name:
                logger.info(f"/bought fallback parsing: extracted item_name='{item_name}'")

        if not item_name:
            await update.message.reply_text(
                "📝 Usage:\n"
                "• `/bought <item>` - mark specific item\n"
                "• `/bought all` - mark all pending items\n"
                "• `/bought /costco` - mark all costco items\n"
                "• `/bought /indian` - mark all indian items\n"
                "• `/bought /misc` - mark all misc items"
            )
            return

        item_name_lower = item_name.lower().lstrip('/')

        # Check for bulk operations and prepare items
        if item_name_lower == "all":
            bought_type = 'bulk'
            store_filter = None
            pending_items = self.sheets.get_pending_items()
            if not pending_items:
                await update.message.reply_text("❌ No pending items found.")
                return
            items = pending_items
            item_list = "\n".join([f"• {i['item']} x{i['quantity']} [/{i.get('store', 'misc')}]" for i in pending_items])
        elif item_name_lower in VALID_STORES:
            bought_type = 'bulk'
            store_filter = item_name_lower
            pending_items = self.sheets.get_pending_items()
            store_items = [i for i in pending_items if i.get('store', 'misc').lower() == item_name_lower]
            if not store_items:
                await update.message.reply_text(f"❌ No pending items found for store '/{item_name_lower}'.")
                return
            items = store_items
            item_list = "\n".join([f"• {i['item']} x{i['quantity']}" for i in store_items])
        else:
            # Single item
            bought_type = 'single'
            store_filter = None
            all_items = self.sheets.get_all_items()
            matching_item = None
            for item in all_items:
                if item["status"].lower() == "pending" and item["item"].lower() == item_name_lower:
                    matching_item = item
                    break

            if not matching_item:
                await update.message.reply_text(
                    f"❌ No pending item found matching: **{item_name}**\n"
                    f"Use `/list` to see pending items."
                )
                return
            items = [matching_item]
            item_list = f"• {matching_item['item']} x{matching_item['quantity']} [/{matching_item.get('store', 'misc')}]"

        # Store state for this user
        _user_bought_state[user.id] = {
            'bought_type': bought_type,
            'store_filter': store_filter,
            'items': items,
            'user': user
        }
        logger.info(f"_handle_bought: stored state for user {user.id}, _user_bought_state now has {len(_user_bought_state)} entries")

        await update.message.reply_text(
            f"🛒 **Mark as bought:**\n{item_list}\n\n"
            f"💰 Enter amount spent (e.g., `75` or `120.50`):"
        )

    def _extract_bought_item(self, text: str) -> str:
        """Extract item name from /bought command text (with or without @botname)."""
        text = text.strip()
        # Match /bought or /bought@botname followed by item name
        match = re.match(r"^/bought(?:@\w+)?\s+(.+)$", text, re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    async def _handle_bought_amount(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle amount input for /bought flow (when user has pending bought state)."""
        user = update.effective_user
        logger.info(f"_handle_bought_amount called: user_id={user.id if user else None}, _user_bought_state keys={list(_user_bought_state.keys())}")
        if not user or user.id not in _user_bought_state:
            logger.info(f"User {user.id if user else None} not in _user_bought_state, skipping")
            return  # Not in bought flow, let other handlers process

        text = update.message.text.strip()

        # Validate amount
        try:
            amount = float(text)
            if amount < 0:
                await update.message.reply_text("❌ Amount cannot be negative. Please enter a positive number:")
                return
        except ValueError:
            await update.message.reply_text("❌ Invalid amount. Please enter a number (e.g., `75` or `120.50`):")
            return

        # Get stored state
        state = _user_bought_state.pop(user.id)
        bought_type = state.get('bought_type')
        items = state.get('items', [])
        store_filter = state.get('store_filter')
        amount_str = str(amount)

        try:
            if bought_type == 'bulk':
                results = self.sheets.mark_items_bought_bulk(store_filter=store_filter, amount=amount_str)
                if results:
                    filter_text = f" for store '/{store_filter}'" if store_filter else ""
                    lines = [f"✅ **Marked {len(results)} item(s) as purchased{filter_text}!**\n"]
                    for result in results:
                        lines.append(
                            f"• {result['item']} x{result['quantity']} "
                            f"[{result.get('store', 'misc')}] — {result['user_name']}"
                        )
                    lines.append(f"\n💰 Amount: **{amount_str}**")
                    lines.append(f"📅 Date: {results[0]['bought_date']}")
                    await update.message.reply_text("\n".join(lines))
                else:
                    await update.message.reply_text("❌ No items were updated.")
            else:
                # Single item
                item_name = items[0]['item']
                result = self.sheets.mark_item_bought(item_name, amount=amount_str)
                if result:
                    await update.message.reply_text(
                        f"✅ **Marked as purchased!**\n"
                        f"• Item: {result['item']}\n"
                        f"• Quantity: {result['quantity']}\n"
                        f"• Added by: {result['user_name']}\n"
                        f"• Date: {result['bought_date']}\n"
                        f"• Amount: **{amount_str}**"
                    )
                else:
                    await update.message.reply_text("❌ Failed to update item.")

        except Exception as e:
            logger.error(f"Error marking items as bought: {e}")
            await update.message.reply_text("⚠️ Failed to update items. Please try again.")

    async def _handle_bought_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Cancel the /bought flow."""
        user = update.effective_user
        if user and user.id in _user_bought_state:
            _user_bought_state.pop(user.id)
        await update.message.reply_text("❌ Cancelled.")

    async def _handle_bought_fallback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Fallback handler for /bought command when CommandHandler doesn't match (missing entities)."""
        if not update.message or not update.message.text:
            return

        user = update.effective_user
        if not user or not self._is_user_allowed(user.id):
            return  # Silently ignore - authorization handled by main handler

        item_name = self._extract_bought_item(update.message.text)
        if not item_name:
            return  # Not a /bought command

        logger.info(f"/bought fallback handler triggered: item_name='{item_name}', user={user.id}")

        # Reuse the main handler logic
        await self._handle_bought(update, context)
        logger.info(f"_handle_bought_fallback completed, _user_bought_state has {len(_user_bought_state)} entries")

    async def _process_bought_bulk(self, update: Update, user, store_filter: str = None):
        """Process bulk bought operation (called from fallback handler)."""
        # Get pending items
        pending_items = self.sheets.get_pending_items()
        if store_filter:
            items = [i for i in pending_items if i.get('store', 'misc').lower() == store_filter.lower()]
        else:
            items = pending_items

        if not items:
            filter_text = f" for store '/{store_filter}'" if store_filter else ""
            await update.message.reply_text(f"❌ No pending items found{filter_text}.")
            return

        # Store state for this user
        _user_bought_state[user.id] = {
            'bought_type': 'bulk',
            'store_filter': store_filter,
            'items': items,
            'user': user
        }

        item_list = "\n".join([f"• {i['item']} x{i['quantity']}" for i in items])
        filter_text = f" for store '/{store_filter}'" if store_filter else ""
        await update.message.reply_text(
            f"🛒 **Mark ALL {len(items)} pending item(s){filter_text} as bought:**\n\n{item_list}\n\n"
            f"💰 Enter total amount spent (e.g., `75` or `120.50`):"
        )

    async def _process_bought_item(self, update: Update, item_name: str, user):
        """Process single item bought operation (called from fallback handler)."""
        all_items = self.sheets.get_all_items()
        matching_item = None
        for item in all_items:
            if item["status"].lower() == "pending" and item["item"].lower() == item_name.lower():
                matching_item = item
                break

        if not matching_item:
            await update.message.reply_text(
                f"❌ No pending item found matching: **{item_name}**\n"
                f"Use `/list` to see pending items."
            )
            return

        # Store state for this user
        _user_bought_state[user.id] = {
            'bought_type': 'single',
            'store_filter': None,
            'items': [matching_item],
            'user': user
        }

        await update.message.reply_text(
            f"🛒 **Mark as bought:** {matching_item['item']} x{matching_item['quantity']} [/{matching_item.get('store', 'misc')}]\n\n"
            f"💰 Enter amount spent (e.g., `75` or `120.50`):"
        )

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
        self._application.add_handler(CommandHandler("spending", self._handle_spending))
        self._application.add_handler(CommandHandler("bought", self._handle_bought))
        self._application.add_handler(CommandHandler("cancel", self._handle_bought_cancel))

        # Handle amount input for /bought flow (check user state first)
        self._application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            self._handle_bought_amount
        ))

        # Fallback handler for /bought when CommandHandler doesn't match (missing entities)
        self._application.add_handler(MessageHandler(
            filters.TEXT & filters.Regex(r"^/bought(?:@\w+)?\b"),
            self._handle_bought_fallback
        ))

        # Main message handler for adding items
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