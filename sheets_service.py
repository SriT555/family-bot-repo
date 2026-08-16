"""Google Sheets service for storing shopping list items."""
import logging
from datetime import datetime
from typing import List, Optional, Dict, Any
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import get_config

logger = logging.getLogger(__name__)

# Scopes required for Google Sheets API
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column headers for the shopping list
SHEET_HEADERS = [
    "Timestamp",
    "User Name",
    "User ID",
    "Item",
    "Quantity",
    "Status"
]


class SheetsService:
    """Service for interacting with Google Sheets API."""

    def __init__(self):
        self._service = None
        self._spreadsheet_id = None
        self._sheet_name = None
        self._sheet_id = None  # Numeric sheet ID for batch operations

    def _get_credentials(self) -> Credentials:
        """Load and return service account credentials."""
        config = get_config()
        creds_path = Path(config.google_sheets.credentials_file)

        if not creds_path.exists():
            raise FileNotFoundError(
                f"Google credentials file not found: {creds_path}. "
                "Please download your service account JSON key and place it at this path."
            )

        credentials = Credentials.from_service_account_file(
            str(creds_path), scopes=SCOPES
        )
        return credentials

    def _build_service(self):
        """Build and cache the Google Sheets API service."""
        if self._service is None:
            credentials = self._get_credentials()
            self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        return self._service

    def _get_sheet_id(self, service) -> int:
        """Get the numeric sheet ID for the configured sheet name."""
        if self._sheet_id is not None:
            return self._sheet_id

        config = get_config()
        spreadsheet_id = config.google_sheets.spreadsheet_id
        logger.info(f"Spreadsheet ID length: {len(spreadsheet_id)}, repr: {repr(spreadsheet_id)}")
        spreadsheet = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()

        for sheet in spreadsheet.get("sheets", []):
            if sheet["properties"]["title"] == config.google_sheets.sheet_name:
                self._sheet_id = sheet["properties"]["sheetId"]
                return self._sheet_id

        raise ValueError(f"Sheet '{config.google_sheets.sheet_name}' not found in spreadsheet")

    def initialize(self) -> bool:
        """
        Initialize the sheet with headers if empty.
        Returns True if initialization was needed, False if already initialized.
        """
        config = get_config()
        service = self._build_service()

        # Check if sheet has data
        range_name = f"{config.google_sheets.sheet_name}!A1:F1"
        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name
            ).execute()

            values = result.get("values", [])

            # If first row is empty or doesn't match headers, initialize
            if not values or values[0] != SHEET_HEADERS:
                logger.info("Initializing sheet with headers")
                self._write_headers(service)
                return True

            logger.info("Sheet already initialized with headers")
            return False

        except HttpError as e:
            logger.error(f"Error checking sheet initialization: {e}")
            raise

    def _write_headers(self, service):
        """Write header row to the sheet."""
        config = get_config()
        body = {
            "values": [SHEET_HEADERS]
        }
        service.spreadsheets().values().update(
            spreadsheetId=config.google_sheets.spreadsheet_id,
            range=f"{config.google_sheets.sheet_name}!A1:F1",
            valueInputOption="RAW",
            body=body
        ).execute()

        # Format header row (bold, frozen)
        self._format_header_row(service)

    def _format_header_row(self, service):
        """Apply formatting to header row: bold and freeze."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        requests = [
            # Bold header row
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True},
                            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor)"
                }
            },
            # Freeze header row
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1}
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            },
            # Auto-resize columns
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 6
                    }
                }
            }
        ]

        body = {"requests": requests}
        service.spreadsheets().batchUpdate(
            spreadsheetId=config.google_sheets.spreadsheet_id,
            body=body
        ).execute()

    def append_item(
        self,
        user_name: str,
        user_id: int,
        item: str,
        quantity: str = "1",
        status: str = "pending"
    ) -> Dict[str, Any]:
        """
        Append a shopping item to the sheet.

        Args:
            user_name: Telegram user's first name or username
            user_id: Telegram user ID
            item: Item name
            quantity: Quantity as string (e.g., "2", "1kg", "500g")
            status: Item status (pending, bought, etc.)

        Returns:
            API response with updated range info
        """
        config = get_config()
        service = self._build_service()

        # Ensure sheet is initialized
        self.initialize()

        # Prepare row data
        timestamp = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        row = [timestamp, user_name, str(user_id), item, quantity, status]

        body = {
            "values": [row]
        }

        result = service.spreadsheets().values().append(
            spreadsheetId=config.google_sheets.spreadsheet_id,
            range=f"{config.google_sheets.sheet_name}!A:F",
            valueInputOption="USER_ENTERED",
            insertDataOption="INSERT_ROWS",
            body=body
        ).execute()

        logger.info(f"Added item: {item} (qty: {quantity}) for user {user_name} ({user_id})")
        return result

    def get_all_items(self) -> List[Dict[str, Any]]:
        """Retrieve all items from the sheet as list of dicts."""
        config = get_config()
        service = self._build_service()

        range_name = f"{config.google_sheets.sheet_name}!A2:F"  # Skip header
        result = service.spreadsheets().values().get(
            spreadsheetId=config.google_sheets.spreadsheet_id,
            range=range_name
        ).execute()

        values = result.get("values", [])
        items = []

        for i, row in enumerate(values, start=2):  # Row 2 = first data row
            # Pad row to ensure all columns exist
            padded_row = row + [""] * (6 - len(row))
            items.append({
                "row": i,
                "timestamp": padded_row[0],
                "user_name": padded_row[1],
                "user_id": padded_row[2],
                "item": padded_row[3],
                "quantity": padded_row[4],
                "status": padded_row[5] or "pending"
            })

        return items

    def update_status(self, row: int, status: str) -> Dict[str, Any]:
        """Update the status of an item at a specific row."""
        config = get_config()
        service = self._build_service()

        # Status is in column F (index 5)
        range_name = f"{config.google_sheets.sheet_name}!F{row}"

        body = {"values": [[status]]}
        result = service.spreadsheets().values().update(
            spreadsheetId=config.google_sheets.spreadsheet_id,
            range=range_name,
            valueInputOption="RAW",
            body=body
        ).execute()

        logger.info(f"Updated row {row} status to: {status}")
        return result

    def get_pending_items(self) -> List[Dict[str, Any]]:
        """Get only pending items."""
        all_items = self.get_all_items()
        return [item for item in all_items if item["status"].lower() == "pending"]


# Global service instance
_sheets_service: Optional[SheetsService] = None


def get_sheets_service() -> SheetsService:
    """Get the global sheets service instance (singleton)."""
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = SheetsService()
    return _sheets_service