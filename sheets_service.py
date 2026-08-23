"""Google Sheets service for storing shopping list items."""
import logging
import re
import time
from datetime import datetime
from typing import List, Optional, Dict, Any, Callable, TypeVar
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from config import get_config

logger = logging.getLogger(__name__)

# Scopes required for Google Sheets API
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Valid store categories
VALID_STORES = ["costco", "indian", "misc"]

# Column headers for the shopping list (7 columns with Store + Amount)
SHEET_HEADERS = [
    "Timestamp",
    "User Name",
    "Item",
    "Quantity",
    "Store",
    "Status",
    "Amount"
]

# Retry configuration for transient errors
MAX_RETRIES = 3
BASE_DELAY = 1.0  # seconds
MAX_DELAY = 10.0  # seconds

# Error patterns that indicate transient failures
TRANSIENT_ERROR_PATTERNS = [
    "BrokenPipeError",
    "SSL: UNEXPECTED_EOF_WHILE_READING",
    "Connection reset",
    "Connection timed out",
    "Read timed out",
    "Temporary failure",
    "Service unavailable",
    "503",
    "502",
    "504",
]

# Monthly header pattern
MONTH_HEADER_PATTERN = r"^=== .+ ===$"

T = TypeVar("T")


def _get_current_month_key() -> str:
    """Return current month key in 'Month YYYY' format (UTC)."""
    return datetime.utcnow().strftime("%B %Y")


def _is_month_header_row(row: List[str]) -> bool:
    """Check if a row is a month header row."""
    if not row or len(row) < 1:
        return False
    # Month header has "=== Month YYYY ===" in first column (index 0)
    item_text = row[0].strip()
    return bool(re.match(MONTH_HEADER_PATTERN, item_text))


def _is_transient_error(error: Exception) -> bool:
    """Check if an error is transient and worth retrying."""
    error_str = str(error)
    return any(pattern in error_str for pattern in TRANSIENT_ERROR_PATTERNS)


def _retry_with_backoff(func: Callable[[], T], max_retries: int = MAX_RETRIES) -> T:
    """Execute a function with exponential backoff retry for transient errors."""
    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return func()
        except Exception as e:
            last_error = e
            if attempt < max_retries and _is_transient_error(e):
                delay = min(BASE_DELAY * (2 ** attempt), MAX_DELAY)
                logger.warning(f"Transient error (attempt {attempt + 1}/{max_retries + 1}): {e}. Retrying in {delay:.1f}s...")
                time.sleep(delay)
            else:
                # Non-transient error or max retries exceeded
                raise
    raise last_error


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

    def _execute_with_retry(self, request_builder: Callable[[], Any]) -> Any:
        """Execute a Google Sheets API request with retry logic for transient errors."""
        return _retry_with_backoff(request_builder)

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

        # Check if sheet has data (7 columns now: A-G)
        range_name = f"{config.google_sheets.sheet_name}!A1:G1"
        try:
            def _get_values():
                return service.spreadsheets().values().get(
                    spreadsheetId=config.google_sheets.spreadsheet_id,
                    range=range_name
                ).execute()

            result = self._execute_with_retry(_get_values)
            values = result.get("values", [])

            # If first row is empty or doesn't match headers, initialize
            if not values or values[0] != SHEET_HEADERS:
                logger.info("Initializing sheet with headers")
                self._write_headers(service)
                # Apply professional formatting
                logger.info("Applying professional formatting")
                self._apply_professional_formatting(service)
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

        def _update():
            return service.spreadsheets().values().update(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=f"{config.google_sheets.sheet_name}!A1:G1",
                valueInputOption="RAW",
                body=body
            ).execute()

        self._execute_with_retry(_update)

        # Format header row (bold, frozen)
        self._format_header_row(service)

    def _format_header_row(self, service):
        """Apply formatting to header row: bold, professional colors, freeze."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        requests = [
            # Professional header row: 11pt bold, dark gray bg, dark text
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 11, "foregroundColor": {"red": 0.13, "green": 0.13, "blue": 0.13}},
                            "backgroundColor": {"red": 0.96, "green": 0.96, "blue": 0.96}
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
            # Auto-resize columns (7 columns: Timestamp, User Name, Item, Quantity, Store, Status, Amount)
            {
                "autoResizeDimensions": {
                    "dimensions": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": 0,
                        "endIndex": 7
                    }
                }
            }
        ]

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _format_month_header_row(self, service, row_index: int):
        """Apply formatting to month header row: bold, background color, merge cells."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        requests = [
            # Bold and background color for month header row
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_index,
                        "endRowIndex": row_index + 1,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"bold": True, "fontSize": 13, "foregroundColor": {"red": 1, "green": 1, "blue": 1}},
                            "backgroundColor": {"red": 0.15, "green": 0.25, "blue": 0.35},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,backgroundColor,horizontalAlignment)"
                }
            },
            # Merge cells A-G for visual span (7 columns)
            {
                "mergeCells": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_index,
                        "endRowIndex": row_index + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "mergeType": "MERGE_ALL"
                }
            }
        ]

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _apply_professional_formatting(self, service):
        """Apply all professional formatting to the sheet."""
        self._apply_column_widths(service)
        self._apply_number_formatting(service)
        self._apply_conditional_formatting(service)
        self._apply_banded_rows(service)
        self._apply_borders(service)
        self._apply_data_validation(service)
        self._apply_filters(service)

    def _apply_column_widths(self, service):
        """Set professional column widths."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        # Column widths in pixels: A=Timestamp, B=User, C=Item, D=Qty, E=Store, F=Status, G=Amount
        column_widths = [160, 140, 250, 100, 100, 140, 120]

        requests = []
        for i, width in enumerate(column_widths):
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "COLUMNS",
                        "startIndex": i,
                        "endIndex": i + 1
                    },
                    "properties": {"pixelSize": width},
                    "fields": "pixelSize"
                }
            })

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _get_currency_format(self, currency: str) -> dict:
        """Get currency format pattern and symbol for the given currency code."""
        currency = currency.upper()
        formats = {
            "USD": {"symbol": "$", "pattern": "$#,##0.00"},
            "INR": {"symbol": "₹", "pattern": "₹#,##0.00"},
            "EUR": {"symbol": "€", "pattern": "€#,##0.00"},
            "GBP": {"symbol": "£", "pattern": "£#,##0.00"},
        }
        return formats.get(currency, formats["USD"])

    def _apply_number_formatting(self, service):
        """Apply number/currency formatting to Quantity and Amount columns."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        # Get currency format from config
        currency = config.google_sheets.currency.upper()
        currency_fmt = self._get_currency_format(currency)

        requests = [
            # Column D (Quantity) - number format with 2 decimal places, 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,  # Skip header
                        "startColumnIndex": 3,
                        "endColumnIndex": 4
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "numberFormat": {
                                "type": "NUMBER",
                                "pattern": "#,##0.##"
                            },
                            "horizontalAlignment": "RIGHT"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,numberFormat,horizontalAlignment)"
                }
            },
            # Column G (Amount) - configurable currency format, 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,  # Skip header
                        "startColumnIndex": 6,
                        "endColumnIndex": 7
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "numberFormat": {
                                "type": "CURRENCY",
                                "pattern": currency_fmt["pattern"]
                            },
                            "horizontalAlignment": "RIGHT"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,numberFormat,horizontalAlignment)"
                }
            },
            # Column E (Store) - center align, 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,horizontalAlignment)"
                }
            },
            # Column F (Status) - center align, 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 5,
                        "endColumnIndex": 6
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,horizontalAlignment)"
                }
            },
            # Column A (Timestamp) - 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 1
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat)"
                }
            },
            # Column B (User Name) - 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 1,
                        "endColumnIndex": 2
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat)"
                }
            },
            # Column C (Item) - 10pt font
            {
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 2,
                        "endColumnIndex": 3
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10}
                        }
                    },
                    "fields": "userEnteredFormat(textFormat)"
                }
            }
        ]

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _apply_conditional_formatting(self, service):
        """Apply conditional formatting rules for status, store, and amount."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        requests = [
            # Status column (F) - Green for bought (✅)
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 5,
                            "endColumnIndex": 6
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_STARTS_WITH",
                                "values": [{"userEnteredValue": "✅"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.91, "green": 0.96, "blue": 0.91},  # #E8F5E9
                                "textFormat": {"foregroundColor": {"red": 0.11, "green": 0.37, "blue": 0.13}, "bold": True}  # #1B5E20
                            }
                        }
                    },
                    "index": 0
                }
            },
            # Status column (F) - Orange for pending
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 5,
                            "endColumnIndex": 6
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "pending"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 1.0, "green": 0.95, "blue": 0.88},  # #FFF3E0
                                "textFormat": {"foregroundColor": {"red": 0.90, "green": 0.32, "blue": 0.0}, "bold": True}  # #E65100
                            }
                        }
                    },
                    "index": 1
                }
            },
            # Store column (E) - Blue for costco
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 4,
                            "endColumnIndex": 5
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "costco"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.89, "green": 0.95, "blue": 0.99},  # #E3F2FD
                                "textFormat": {"foregroundColor": {"red": 0.08, "green": 0.40, "blue": 0.77}, "bold": True}  # #1565C0
                            }
                        }
                    },
                    "index": 2
                }
            },
            # Store column (E) - Purple for indian
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 4,
                            "endColumnIndex": 5
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "indian"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.95, "green": 0.90, "blue": 0.96},  # #F3E5F5
                                "textFormat": {"foregroundColor": {"red": 0.42, "green": 0.11, "blue": 0.60}, "bold": True}  # #6A1B9A
                            }
                        }
                    },
                    "index": 3
                }
            },
            # Store column (E) - Gray for misc
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 4,
                            "endColumnIndex": 5
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "TEXT_EQ",
                                "values": [{"userEnteredValue": "misc"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.96, "green": 0.96, "blue": 0.96},  # #F5F5F5
                                "textFormat": {"foregroundColor": {"red": 0.26, "green": 0.26, "blue": 0.26}, "bold": True}  # #424242
                            }
                        }
                    },
                    "index": 4
                }
            },
            # Amount column (G) - Green tint for positive amounts (> 0)
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 6,
                            "endColumnIndex": 7
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "NUMBER_GREATER",
                                "values": [{"userEnteredValue": "0"}]
                            },
                            "format": {
                                "backgroundColor": {"red": 0.91, "green": 0.96, "blue": 0.91},  # #E8F5E9
                                "textFormat": {"foregroundColor": {"red": 0.11, "green": 0.37, "blue": 0.13}, "bold": True}  # #1B5E20
                            }
                        }
                    },
                    "index": 5
                }
            },
            # Amount column (G) - Gray for empty/zero amounts
            {
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": 6,
                            "endColumnIndex": 7
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "NUMBER_EQ",
                                "values": [{"userEnteredValue": "0"}]
                            },
                            "format": {
                                "textFormat": {"foregroundColor": {"red": 0.5, "green": 0.5, "blue": 0.5}}  # #808080
                            }
                        }
                    },
                    "index": 6
                }
            }
        ]

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _apply_banded_rows(self, service):
        """Apply alternating row colors for data rows (skipping month headers) with subtle store-aware tinting."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        # Get all rows to identify month header rows and store values
        range_name = f"{config.google_sheets.sheet_name}!A:G"

        def _get_values():
            return service.spreadsheets().values().get(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name
            ).execute()

        result = self._execute_with_retry(_get_values)
        values = result.get("values", [])

        # Find month header rows (0-based indices)
        month_header_rows = set()
        for i, row in enumerate(values):
            if _is_month_header_row(row):
                month_header_rows.add(i)

        # Build banded row requests for non-header rows
        requests = []
        band_index = 0

        for i in range(1, len(values)):  # Skip header row (0)
            if i in month_header_rows:
                continue

            # Get store value for this row (column E, index 4)
            row_data = values[i] if i < len(values) else []
            store = (row_data[4] if len(row_data) > 4 else "misc").strip().lower()

            # Base alternating colors with subtle store tinting (2% hue shift)
            if band_index % 2 == 0:
                # Even bands: light gray base
                if store == "costco":
                    bg_color = {"red": 0.96, "green": 0.98, "blue": 1.0}      # #F5FAFF - subtle blue
                elif store == "indian":
                    bg_color = {"red": 0.98, "green": 0.96, "blue": 1.0}      # #FAF5FF - subtle purple
                else:  # misc
                    bg_color = {"red": 0.98, "green": 0.98, "blue": 0.98}      # #FAFAFA - neutral gray
            else:
                # Odd bands: white base
                if store == "costco":
                    bg_color = {"red": 0.99, "green": 0.995, "blue": 1.0}     # #FCFCFF - very subtle blue
                elif store == "indian":
                    bg_color = {"red": 0.995, "green": 0.99, "blue": 1.0}     # #FEFCFF - very subtle purple
                else:  # misc
                    bg_color = {"red": 1.0, "green": 1.0, "blue": 1.0}        # #FFFFFF - pure white

            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg_color
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor)"
                }
            })
            band_index += 1

        if requests:
            body = {"requests": requests}

            def _batch_update():
                return service.spreadsheets().batchUpdate(
                    spreadsheetId=config.google_sheets.spreadsheet_id,
                    body=body
                ).execute()

            self._execute_with_retry(_batch_update)

    def _apply_borders(self, service):
        """Apply professional borders to the sheet."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        # Get all rows to identify month header rows
        range_name = f"{config.google_sheets.sheet_name}!A:A"

        def _get_values():
            return service.spreadsheets().values().get(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name
            ).execute()

        result = self._execute_with_retry(_get_values)
        values = result.get("values", [])

        # Find month header rows
        month_header_rows = set()
        for i, row in enumerate(values):
            if _is_month_header_row(row):
                month_header_rows.add(i)

        requests = [
            # Grid borders for all data cells (light gray)
            {
                "updateBorders": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(values),
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "top": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "bottom": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "left": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "right": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "innerHorizontal": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "innerVertical": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}}
                }
            },
            # Thicker border under header row
            {
                "updateBorders": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "endRowIndex": 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "bottom": {"style": "SOLID", "width": 2, "color": {"red": 0.6, "green": 0.6, "blue": 0.6}}
                }
            }
        ]

        # Add navy borders for month header rows
        for row_idx in month_header_rows:
            requests.append({
                "updateBorders": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": row_idx,
                        "endRowIndex": row_idx + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "top": {"style": "SOLID", "width": 2, "color": {"red": 0.15, "green": 0.25, "blue": 0.35}},
                    "bottom": {"style": "SOLID", "width": 2, "color": {"red": 0.15, "green": 0.25, "blue": 0.35}}
                }
            })

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _apply_data_validation(self, service):
        """Apply data validation dropdowns for Store and Status columns."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        requests = [
            # Column E (Store) - dropdown validation
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "costco"},
                                {"userEnteredValue": "indian"},
                                {"userEnteredValue": "misc"}
                            ]
                        },
                        "inputMessage": "Select store: costco, indian, or misc",
                        "strict": True,
                        "showCustomUi": True
                    }
                }
            },
            # Column F (Status) - dropdown validation
            {
                "setDataValidation": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "startColumnIndex": 5,
                        "endColumnIndex": 6
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [
                                {"userEnteredValue": "pending"},
                                {"userEnteredValue": "✅"}
                            ]
                        },
                        "inputMessage": "Status: pending or ✅ (auto-filled when marking bought)",
                        "strict": False,
                        "showCustomUi": True
                    }
                }
            }
        ]

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _apply_filters(self, service):
        """Enable filter view on header row."""
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        requests = [{
            "setBasicFilter": {
                "filter": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": 0,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    }
                }
            }
        }]

        body = {"requests": requests}

        def _batch_update():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_update)

    def _apply_formatting_to_new_rows(self, service, start_row: int, end_row: int):
        """
        Apply professional formatting to a specific range of new rows.
        This ensures banded rows, borders, and number formatting extend to new data.

        Args:
            service: Google Sheets service
            start_row: 0-based start row index (inclusive)
            end_row: 0-based end row index (exclusive)
        """
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        # Get all rows to identify month header rows and store values
        range_name = f"{config.google_sheets.sheet_name}!A:G"

        def _get_values():
            return service.spreadsheets().values().get(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name
            ).execute()

        result = self._execute_with_retry(_get_values)
        values = result.get("values", [])

        # Find month header rows in the range
        month_header_rows = set()
        for i in range(start_row, min(end_row, len(values))):
            if i < len(values) and _is_month_header_row(values[i]):
                month_header_rows.add(i)

        requests = []
        band_index = 0

        # Count existing data rows before start_row to maintain banding continuity
        for i in range(1, start_row):
            if i < len(values) and i not in month_header_rows:
                band_index += 1

        for i in range(start_row, min(end_row, len(values))):
            if i in month_header_rows:
                continue

            # Get store value for this row (column E, index 4)
            row_data = values[i] if i < len(values) else []
            store = (row_data[4] if len(row_data) > 4 else "misc").strip().lower()

            # Base alternating colors with subtle store tinting (2% hue shift)
            if band_index % 2 == 0:
                if store == "costco":
                    bg_color = {"red": 0.96, "green": 0.98, "blue": 1.0}
                elif store == "indian":
                    bg_color = {"red": 0.98, "green": 0.96, "blue": 1.0}
                else:
                    bg_color = {"red": 0.98, "green": 0.98, "blue": 0.98}
            else:
                if store == "costco":
                    bg_color = {"red": 0.99, "green": 0.995, "blue": 1.0}
                elif store == "indian":
                    bg_color = {"red": 0.995, "green": 0.99, "blue": 1.0}
                else:
                    bg_color = {"red": 1.0, "green": 1.0, "blue": 1.0}

            # Apply background color
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": bg_color
                        }
                    },
                    "fields": "userEnteredFormat(backgroundColor)"
                }
            })

            # Apply borders to this row
            requests.append({
                "updateBorders": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 0,
                        "endColumnIndex": 7
                    },
                    "top": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "bottom": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "left": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "right": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "innerHorizontal": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}},
                    "innerVertical": {"style": "SOLID", "width": 1, "color": {"red": 0.88, "green": 0.88, "blue": 0.88}}
                }
            })

            # Apply number formatting to this row (Quantity, Amount, Store, Status, Timestamp, User, Item)
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 3,
                        "endColumnIndex": 4
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "numberFormat": {
                                "type": "NUMBER",
                                "pattern": "#,##0.##"
                            },
                            "horizontalAlignment": "RIGHT"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,numberFormat,horizontalAlignment)"
                }
            })
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 6,
                        "endColumnIndex": 7
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "numberFormat": {
                                "type": "CURRENCY",
                                "pattern": self._get_currency_format(config.google_sheets.currency)["pattern"]
                            },
                            "horizontalAlignment": "RIGHT"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,numberFormat,horizontalAlignment)"
                }
            })
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 4,
                        "endColumnIndex": 5
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,horizontalAlignment)"
                }
            })
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": sheet_id,
                        "startRowIndex": i,
                        "endRowIndex": i + 1,
                        "startColumnIndex": 5,
                        "endColumnIndex": 6
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "textFormat": {"fontSize": 10},
                            "horizontalAlignment": "CENTER"
                        }
                    },
                    "fields": "userEnteredFormat(textFormat,horizontalAlignment)"
                }
            })
            for col_idx in [0, 1, 2]:
                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": i,
                            "endRowIndex": i + 1,
                            "startColumnIndex": col_idx,
                            "endColumnIndex": col_idx + 1
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "textFormat": {"fontSize": 10}
                            }
                        },
                        "fields": "userEnteredFormat(textFormat)"
                    }
                })

            band_index += 1

        if requests:
            body = {"requests": requests}

            def _batch_update():
                return service.spreadsheets().batchUpdate(
                    spreadsheetId=config.google_sheets.spreadsheet_id,
                    body=body
                ).execute()

            self._execute_with_retry(_batch_update)

    def _get_last_month_header_row(self, service) -> tuple[Optional[int], Optional[str]]:
        """
        Find the last month header row in the sheet.
        Returns (row_index, month_key) where row_index is 0-based, or (None, None) if not found.
        """
        config = get_config()
        # Read all data to find month headers (check column A only)
        range_name = f"{config.google_sheets.sheet_name}!A:A"

        def _get_values():
            return service.spreadsheets().values().get(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name
            ).execute()

        result = self._execute_with_retry(_get_values)
        values = result.get("values", [])

        last_header_row = None
        last_month_key = None

        for i, row in enumerate(values):
            if _is_month_header_row(row):
                last_header_row = i  # 0-based index
                # Extract month key from header (e.g., "=== August 2026 ===" -> "August 2026")
                item_text = row[0].strip() if len(row) > 0 else ""
                match = re.match(r"^=== (.+) ===$", item_text)
                if match:
                    last_month_key = match.group(1)

        return last_header_row, last_month_key

    def _insert_month_header(self, service, month_key: str) -> int:
        """
        Insert a month header row at the correct position using batchUpdate.
        Returns the row index (0-based) where header was inserted.
        """
        config = get_config()
        sheet_id = self._get_sheet_id(service)

        # Determine where to insert: after the last month header, or at row 1 (after column headers)
        last_header_row, _ = self._get_last_month_header_row(service)

        if last_header_row is not None:
            # Insert after the last month header
            insert_row = last_header_row + 1
        else:
            # First month header - insert at row 1 (after column headers at row 0)
            insert_row = 1

        header_text = f"=== {month_key} ==="

        # Use batchUpdate to insert a row at the specific position, then write the value
        requests = [
            # Insert a new row at insert_row
            {
                "insertDimension": {
                    "range": {
                        "sheetId": sheet_id,
                        "dimension": "ROWS",
                        "startIndex": insert_row,
                        "endIndex": insert_row + 1
                    },
                    "inheritFromBefore": False
                }
            }
        ]

        body = {"requests": requests}

        def _batch_insert():
            return service.spreadsheets().batchUpdate(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                body=body
            ).execute()

        self._execute_with_retry(_batch_insert)

        # Now write the header value to the inserted row (no timestamp, just header text)
        # Row index in API is 1-based for values().update
        # Format: ["=== Month YYYY ===", "", "", "", "", ""] - text in first cell, merged across all columns
        range_name = f"{config.google_sheets.sheet_name}!A{insert_row + 1}:F{insert_row + 1}"
        value_body = {"values": [[header_text, "", "", "", "", ""]]}

        def _write_header():
            return service.spreadsheets().values().update(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body=value_body
            ).execute()

        self._execute_with_retry(_write_header)

        # Format the header row
        self._format_month_header_row(service, insert_row)

        logger.info(f"Inserted month header for {month_key} at row {insert_row + 1}")
        return insert_row

    def _ensure_month_header(self, service) -> bool:
        """
        Ensure the current month header exists.
        Returns True if a new header was inserted, False if already exists.
        """
        current_month = _get_current_month_key()
        last_header_row, last_month_key = self._get_last_month_header_row(service)

        if last_month_key == current_month:
            # Current month header already exists
            return False

        # Insert new month header
        self._insert_month_header(service, current_month)
        return True

    def append_item(
        self,
        user_name: str,
        item: str,
        quantity: str = "1",
        store: str = "misc",
        status: str = "pending"
    ) -> Dict[str, Any]:
        """
        Append a shopping item to the sheet.

        Args:
            user_name: Telegram user's first name or username
            item: Item name
            quantity: Quantity as string (e.g., "2", "1kg", "500g")
            store: Store category (costco, indian, misc)
            status: Item status (pending, bought, etc.)

        Returns:
            API response with updated range info
        """
        config = get_config()
        service = self._build_service()

        # Ensure sheet is initialized
        self.initialize()

        # Ensure current month header exists
        self._ensure_month_header(service)

        # Validate store
        store = store.lower().strip()
        if store not in VALID_STORES:
            store = "misc"

        # Prepare row data (7 columns: Timestamp, User Name, Item, Quantity, Store, Status, Amount)
        timestamp = datetime.utcnow().strftime("%d/%m/%Y %H:%M")  # e.g., "22/08/2026 23:30"
        row = [timestamp, user_name, item, quantity, store, status, ""]

        body = {
            "values": [row]
        }

        def _append():
            return service.spreadsheets().values().append(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=f"{config.google_sheets.sheet_name}!A:G",
                valueInputOption="USER_ENTERED",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()

        result = self._execute_with_retry(_append)

        # Apply formatting to the newly added row
        # Extract the updated row from the result
        try:
            updated_range = result.get("updates", {}).get("updatedRange", "")
            if updated_range:
                # Parse row number from range like "Sheet1!A5:G5"
                import re
                match = re.search(r'!A(\d+):', updated_range)
                if match:
                    new_row = int(match.group(1)) - 1  # Convert to 0-based
                    self._apply_formatting_to_new_rows(service, new_row, new_row + 1)
        except Exception as e:
            logger.warning(f"Could not apply formatting to new row: {e}")

        logger.info(f"Added item: {item} (qty: {quantity}, store: {store}) for user {user_name}")
        return result

    def get_all_items(self) -> List[Dict[str, Any]]:
        """Retrieve all items from the sheet as list of dicts (excludes month headers)."""
        config = get_config()
        service = self._build_service()

        range_name = f"{config.google_sheets.sheet_name}!A2:G"  # Skip header, 7 columns

        def _get_values():
            return service.spreadsheets().values().get(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name
            ).execute()

        result = self._execute_with_retry(_get_values)
        values = result.get("values", [])
        items = []

        for i, row in enumerate(values, start=2):  # Row 2 = first data row
            # Pad row to ensure all columns exist (7 columns)
            padded_row = row + [""] * (7 - len(row))

            # Skip month header rows (identified by "=== Month YYYY ===" in Item column)
            item_text = padded_row[2].strip()
            if _is_month_header_row(padded_row):
                continue

            items.append({
                "row": i,
                "timestamp": padded_row[0].strip(),
                "user_name": padded_row[1].strip(),
                "item": item_text,
                "quantity": padded_row[3].strip(),
                "store": (padded_row[4] or "misc").strip(),
                "status": (padded_row[5] or "pending").strip(),
                "amount": padded_row[6].strip() if len(padded_row) > 6 else ""
            })

        return items

    def update_status(self, row: int, status: str) -> Dict[str, Any]:
        """Update the status of an item at a specific row."""
        config = get_config()
        service = self._build_service()

        # Status is in column F (index 5) with Store column added
        range_name = f"{config.google_sheets.sheet_name}!F{row}"

        body = {"values": [[status]]}

        def _update():
            return service.spreadsheets().values().update(
                spreadsheetId=config.google_sheets.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body=body
            ).execute()

        result = self._execute_with_retry(_update)

        logger.info(f"Updated row {row} status to: {status}")
        return result

    def get_pending_items(self) -> List[Dict[str, Any]]:
        """Get only pending items."""
        all_items = self.get_all_items()
        return [item for item in all_items if item["status"].lower() == "pending"]

    def mark_item_bought(self, item_name: str, amount: str = "") -> Optional[Dict[str, Any]]:
        """
        Find a pending item by name (case-insensitive) and mark it as bought with date and amount.

        Args:
            item_name: Name of the item to mark as bought
            amount: Optional amount spent (e.g., "75")

        Returns:
            Dict with updated item info, or None if not found
        """
        config = get_config()
        service = self._build_service()

        all_items = self.get_all_items()

        # Find matching pending item (case-insensitive)
        for item in all_items:
            if item["status"].lower() == "pending" and item["item"].lower() == item_name.lower():
                row = item["row"]
                bought_date = datetime.utcnow().strftime("%d %b")  # e.g., "22 Aug"
                new_status = f"✅ {bought_date}"

                # Update both Status (column F) and Amount (column G) in one batch
                range_name = f"{config.google_sheets.sheet_name}!F{row}:G{row}"
                body = {"values": [[new_status, amount]]}

                def _update():
                    return service.spreadsheets().values().update(
                        spreadsheetId=config.google_sheets.spreadsheet_id,
                        range=range_name,
                        valueInputOption="RAW",
                        body=body
                    ).execute()

                self._execute_with_retry(_update)

                # Apply formatting to the updated row (ensure borders, number format, etc.)
                try:
                    self._apply_formatting_to_new_rows(service, row - 1, row)
                except Exception as e:
                    logger.warning(f"Could not apply formatting to updated row: {e}")

                logger.info(f"Marked item '{item_name}' as bought on {bought_date} (row {row}), amount: {amount}")
                return {
                    "row": row,
                    "item": item["item"],
                    "quantity": item["quantity"],
                    "user_name": item["user_name"],
                    "store": item.get("store", "misc"),
                    "bought_date": bought_date,
                    "status": new_status,
                    "amount": amount
                }

        return None

    def mark_items_bought_bulk(self, store_filter: str = None, amount: str = "") -> List[Dict[str, Any]]:
        """
        Mark all pending items (optionally filtered by store) as bought with date and amount.

        Args:
            store_filter: Optional store name to filter by (costco, indian, misc)
            amount: Optional total amount spent for all items

        Returns:
            List of dicts with updated item info
        """
        config = get_config()
        service = self._build_service()

        all_items = self.get_all_items()
        bought_date = datetime.utcnow().strftime("%d %b")  # e.g., "22 Aug"
        new_status = f"✅ {bought_date}"
        results = []

        # Find matching pending items
        for item in all_items:
            if item["status"].lower() != "pending":
                continue
            if store_filter and item.get("store", "misc").lower() != store_filter.lower():
                continue

            row = item["row"]
            # Update both Status (column F) and Amount (column G) in one batch
            range_name = f"{config.google_sheets.sheet_name}!F{row}:G{row}"
            body = {"values": [[new_status, amount]]}

            def _update():
                return service.spreadsheets().values().update(
                    spreadsheetId=config.google_sheets.spreadsheet_id,
                    range=range_name,
                    valueInputOption="RAW",
                    body=body
                ).execute()

            self._execute_with_retry(_update)

            # Apply formatting to the updated row
            try:
                self._apply_formatting_to_new_rows(service, row - 1, row)
            except Exception as e:
                logger.warning(f"Could not apply formatting to updated row: {e}")

            logger.info(f"Marked item '{item['item']}' as bought on {bought_date} (row {row}), amount: {amount}")
            results.append({
                "row": row,
                "item": item["item"],
                "quantity": item["quantity"],
                "user_name": item["user_name"],
                "store": item.get("store", "misc"),
                "bought_date": bought_date,
                "status": new_status,
                "amount": amount
            })

        return results

    def get_spending_summary(self, month_key: str = None) -> Dict[str, Any]:
        """
        Get spending summary for a specific month (default: current month).

        Args:
            month_key: Month in "Month YYYY" format (e.g., "August 2026")

        Returns:
            Dict with total, by_store breakdown, and items
        """
        if month_key is None:
            month_key = _get_current_month_key()

        all_items = self.get_all_items()

        # Filter items for the specified month (by timestamp)
        # Timestamp format: "22/08/2026 23:30"
        month_items = []
        for item in all_items:
            if item["status"].startswith("✅") and item.get("amount"):
                try:
                    # Parse timestamp to check month
                    ts = item["timestamp"]
                    if "/" in ts:
                        day, month, year = ts.split("/")[0], ts.split("/")[1], ts.split("/")[2].split(" ")[0]
                        item_month_key = datetime(int(year), int(month), int(day)).strftime("%B %Y")
                        if item_month_key == month_key:
                            month_items.append(item)
                except (ValueError, IndexError):
                    continue

        # Calculate totals by store
        by_store = {store: 0.0 for store in VALID_STORES}
        total = 0.0

        for item in month_items:
            try:
                amt = float(item.get("amount", 0) or 0)
                store = item.get("store", "misc").lower()
                if store in by_store:
                    by_store[store] += amt
                total += amt
            except (ValueError, TypeError):
                continue

        return {
            "month": month_key,
            "total": round(total, 2),
            "by_store": {k: round(v, 2) for k, v in by_store.items()},
            "items_count": len(month_items),
            "items": month_items
        }


# Global service instance
_sheets_service: Optional[SheetsService] = None


def get_sheets_service() -> SheetsService:
    """Get the global sheets service instance (singleton)."""
    global _sheets_service
    if _sheets_service is None:
        _sheets_service = SheetsService()
    return _sheets_service