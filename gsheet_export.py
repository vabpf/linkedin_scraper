import gspread
import json
from google.oauth2.service_account import Credentials

def _normalize_cell_value(value):
    """Convert values to sheet-safe scalar strings/numbers."""
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


def _derive_headers(data):
    """Derive stable headers from records preserving first-seen order."""
    headers = []
    seen = set()
    for record in data:
        for key in record.keys():
            if key not in seen:
                seen.add(key)
                headers.append(key)
    return headers


def _normalize_key(value):
    """Normalize key values for dedup checks."""
    if value is None:
        return ""
    return str(value).strip()


def export_to_gsheet(
    data,
    spreadsheet_id,
    worksheet_name,
    creds_json,
    headers=None,
    unique_key=None,
):
    """
    Appends a list of dict rows to a Google Sheet.
    Args:
        data: List[dict] - Each dict represents a row.
        spreadsheet_id: str - Google Sheet ID (from sheet URL).
        worksheet_name: str - Name of the tab/sheet (e.g. 'Sheet1').
        creds_json: str - Path to GCP service account credentials JSON.
        headers: Optional[List[str]] - Preferred header order/schema.
        unique_key: Optional[str] - Column used to skip duplicate inserts.

    Returns:
        Dict[str, int]: Inserted/skipped row counts.
    """
    if not data:
        return {"inserted": 0, "skipped": 0}

    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_file(creds_json, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(spreadsheet_id)
    worksheet = sheet.worksheet(worksheet_name)

    # Read first row only; use it as canonical header if present.
    existing_header = worksheet.row_values(1)
    header = [h for h in existing_header if h.strip()]

    if not header:
        header = headers[:] if headers else _derive_headers(data)
        worksheet.append_row(header)

    key_index = None
    existing_keys = set()
    if unique_key:
        if unique_key not in header:
            raise ValueError(f"unique_key '{unique_key}' is not present in sheet header")
        key_index = header.index(unique_key) + 1
        existing_values = worksheet.col_values(key_index)
        for value in existing_values[1:]:
            normalized = _normalize_key(value)
            if normalized:
                existing_keys.add(normalized)

    rows = []
    skipped = 0
    for record in data:
        if unique_key:
            key_value = _normalize_key(record.get(unique_key))
            if key_value and key_value in existing_keys:
                skipped += 1
                continue
            if key_value:
                existing_keys.add(key_value)

        row = [_normalize_cell_value(record.get(col, "")) for col in header]
        rows.append(row)

    if rows:
        worksheet.append_rows(rows, value_input_option="USER_ENTERED")

    return {"inserted": len(rows), "skipped": skipped}
