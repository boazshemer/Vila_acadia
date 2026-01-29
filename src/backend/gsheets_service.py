"""
Google Sheets Service for Vila Acadia
Handles all interactions with the Google Sheets database.
"""
import gspread
from google.oauth2.service_account import Credentials
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from .config import settings


class GoogleSheetsService:
    """Service for interacting with Google Sheets as a database."""
    
    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    
    # Sheet configuration
    SETTINGS_TAB = "Settings"
    MONTH_SHEET_HEADERS = [
        "Employee", "Date", "Clock In", "Clock Out", "Hours Worked",
        "Tips Collected", "Tip Rate (per hour)", "Payout"
    ]
    
    def __init__(self):
        """Initialize the Google Sheets service with authentication."""
        self._client: Optional[gspread.Client] = None
        self._spreadsheet: Optional[gspread.Spreadsheet] = None
    
    def _get_credentials(self) -> Credentials:
        """Create credentials from service account JSON."""
        creds_dict = settings.get_service_account_dict()
        return Credentials.from_service_account_info(creds_dict, scopes=self.SCOPES)
    
    def connect(self) -> None:
        """Establish connection to Google Sheets."""
        if self._client is None:
            creds = self._get_credentials()
            self._client = gspread.authorize(creds)
            self._spreadsheet = self._client.open_by_key(settings.google_sheet_id)
    
    def get_spreadsheet(self) -> gspread.Spreadsheet:
        """Get the spreadsheet object, connecting if necessary."""
        if self._spreadsheet is None:
            self.connect()
        return self._spreadsheet
    
    def health_check(self) -> Dict[str, str]:
        """
        Verify connectivity to Google Sheets.
        Returns connection status and spreadsheet info.
        """
        try:
            sheet = self.get_spreadsheet()
            return {
                "status": "connected",
                "spreadsheet_id": settings.google_sheet_id,
                "spreadsheet_title": sheet.title,
                "message": "Successfully connected to Google Sheets"
            }
        except Exception as e:
            return {
                "status": "error",
                "spreadsheet_id": settings.google_sheet_id,
                "message": f"Failed to connect: {str(e)}"
            }
    
    def get_employee_settings(self) -> List[Dict[str, str]]:
        """
        Fetch employee roster and PINs from the Settings tab.
        
        Returns:
            List of dictionaries with 'name' and 'pin' keys.
            Example: [{"name": "John Doe", "pin": "1234"}, ...]
        
        Raises:
            Exception if Settings tab doesn't exist or is malformed.
        """
        try:
            sheet = self.get_spreadsheet()
            settings_worksheet = sheet.worksheet(self.SETTINGS_TAB)
            
            # Get all records from the Settings tab
            # Expected format: Header row with "Name" and "PIN" columns
            records = settings_worksheet.get_all_records()
            
            employees = []
            for record in records:
                # Support both "Name"/"name" and "PIN"/"pin" column names
                name = record.get("Name") or record.get("name", "").strip()
                pin = str(record.get("PIN") or record.get("pin", "")).strip()
                
                if name and pin:
                    employees.append({"name": name, "pin": pin})
            
            return employees
        
        except gspread.WorksheetNotFound:
            raise Exception(f"'{self.SETTINGS_TAB}' tab not found in the spreadsheet")
        except Exception as e:
            raise Exception(f"Error reading employee settings: {str(e)}")
    
    def verify_employee_pin(self, name: str, pin: str) -> bool:
        """
        Verify an employee's PIN against the Settings sheet.
        
        Args:
            name: Employee name
            pin: 4-digit PIN
        
        Returns:
            True if credentials are valid, False otherwise.
        """
        try:
            employees = self.get_employee_settings()
            
            for employee in employees:
                if employee["name"].lower() == name.lower() and employee["pin"] == pin:
                    return True
            
            return False
        
        except Exception:
            # In case of any error, fail closed (deny access)
            return False
    
    def get_or_create_month_sheet(self, date: Optional[datetime] = None) -> gspread.Worksheet:
        """
        Get or create a worksheet for the current/specified month.
        
        Args:
            date: Date to determine month (defaults to current date)
        
        Returns:
            The worksheet for the specified month.
        """
        if date is None:
            date = datetime.now()
        
        # Format: "January 2026"
        sheet_name = date.strftime("%B %Y")
        
        sheet = self.get_spreadsheet()
        
        try:
            # Try to get existing worksheet
            worksheet = sheet.worksheet(sheet_name)
            return worksheet
        
        except gspread.WorksheetNotFound:
            # Create new worksheet with headers
            worksheet = sheet.add_worksheet(
                title=sheet_name,
                rows=100,
                cols=len(self.MONTH_SHEET_HEADERS)
            )
            
            # Set headers in the first row
            worksheet.update([self.MONTH_SHEET_HEADERS], "A1")
            
            # Format headers (bold)
            worksheet.format("A1:H1", {
                "textFormat": {"bold": True},
                "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
            })
            
            return worksheet
    
    def check_entry_exists(self, employee_name: str, date: str, 
                          worksheet: Optional[gspread.Worksheet] = None) -> Tuple[bool, Optional[int]]:
        """
        Check if an entry already exists for the given employee and date.
        This implements the "Read-before-Write" safety guard.
        
        Args:
            employee_name: Name of the employee
            date: Date string (format: YYYY-MM-DD)
            worksheet: Optional worksheet to check (defaults to current month)
        
        Returns:
            Tuple of (exists: bool, row_number: Optional[int])
            If exists is True, row_number indicates where the entry was found.
        """
        if worksheet is None:
            worksheet = self.get_or_create_month_sheet()
        
        try:
            # Get all values from the worksheet
            all_values = worksheet.get_all_values()
            
            # Skip header row (index 0)
            for idx, row in enumerate(all_values[1:], start=2):
                # Check if row has data and matches employee + date
                if len(row) >= 2:
                    row_employee = row[0].strip()
                    row_date = row[1].strip()
                    
                    if row_employee.lower() == employee_name.lower() and row_date == date:
                        return True, idx
            
            return False, None
        
        except Exception as e:
            raise Exception(f"Error checking for existing entry: {str(e)}")
    
    def find_next_empty_row(self, worksheet: Optional[gspread.Worksheet] = None) -> int:
        """
        Find the next empty row in the worksheet.
        
        Args:
            worksheet: Optional worksheet to check (defaults to current month)
        
        Returns:
            Row number of the next empty row.
        """
        if worksheet is None:
            worksheet = self.get_or_create_month_sheet()
        
        all_values = worksheet.get_all_values()
        
        # Find first row where column A (Employee) is empty
        for idx, row in enumerate(all_values[1:], start=2):
            if not row or not row[0].strip():
                return idx
        
        # If all rows have data, return the next row
        return len(all_values) + 1
    
    def calculate_hours(self, start_time: str, end_time: str) -> float:
        """
        Calculate hours worked from start and end time.
        Handles overnight shifts (e.g., 23:00 to 02:00 = 3 hours).
        
        Args:
            start_time: Start time in HH:MM format
            end_time: End time in HH:MM format
        
        Returns:
            Hours worked as a float (rounded to 2 decimals)
        """
        start_hour, start_min = map(int, start_time.split(':'))
        end_hour, end_min = map(int, end_time.split(':'))
        
        # Create datetime objects for calculation
        start_dt = datetime(2000, 1, 1, start_hour, start_min)
        end_dt = datetime(2000, 1, 1, end_hour, end_min)
        
        # If end time is before start time, assume next day
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)
        
        # Calculate difference
        diff = end_dt - start_dt
        hours = diff.total_seconds() / 3600
        
        return round(hours, 2)
    
    def get_or_create_date_column(self, date: str, worksheet: Optional[gspread.Worksheet] = None) -> Tuple[int, bool]:
        """
        Get or create a column for a specific date.
        
        Args:
            date: Date string in YYYY-MM-DD format
            worksheet: Optional worksheet (defaults to current month)
        
        Returns:
            Tuple of (column_index, was_created)
            column_index: 1-based column number
            was_created: True if column was just created
        """
        if worksheet is None:
            date_obj = datetime.strptime(date, "%Y-%m-%d")
            worksheet = self.get_or_create_month_sheet(date_obj)
        
        # Get first row (headers)
        headers = worksheet.row_values(1)
        
        # Check if date column exists
        date_formatted = datetime.strptime(date, "%Y-%m-%d").strftime("%m/%d/%Y")
        
        for idx, header in enumerate(headers, start=1):
            if header == date_formatted:
                return idx, False
        
        # Column doesn't exist, create it
        next_col = len(headers) + 1
        
        # Add date header
        worksheet.update_cell(1, next_col, date_formatted)
        
        # Format header
        worksheet.format(f"{self._col_index_to_letter(next_col)}1", {
            "textFormat": {"bold": True},
            "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}
        })
        
        return next_col, True
    
    def _col_index_to_letter(self, col_index: int) -> str:
        """
        Convert column index (1-based) to letter (A, B, C, ..., Z, AA, AB, ...).
        
        Args:
            col_index: 1-based column index
        
        Returns:
            Column letter(s)
        """
        result = ""
        while col_index > 0:
            col_index -= 1
            result = chr(col_index % 26 + ord('A')) + result
            col_index //= 26
        return result
    
    def get_employee_row(self, employee_name: str, worksheet: Optional[gspread.Worksheet] = None) -> Optional[int]:
        """
        Find the row number for a specific employee.
        
        Args:
            employee_name: Employee name to search for
            worksheet: Optional worksheet (defaults to current month)
        
        Returns:
            Row number (1-based) or None if not found
        """
        if worksheet is None:
            worksheet = self.get_or_create_month_sheet()
        
        # Get all values from column A (employee names)
        col_a = worksheet.col_values(1)
        
        # Search for employee (case-insensitive)
        for idx, name in enumerate(col_a, start=1):
            if name.lower().strip() == employee_name.lower().strip():
                return idx
        
        return None
    
    def get_or_create_employee_row(self, employee_name: str, worksheet: Optional[gspread.Worksheet] = None) -> int:
        """
        Get or create a row for a specific employee.
        
        Args:
            employee_name: Employee name
            worksheet: Optional worksheet (defaults to current month)
        
        Returns:
            Row number (1-based)
        """
        if worksheet is None:
            worksheet = self.get_or_create_month_sheet()
        
        # Check if employee row exists
        row = self.get_employee_row(employee_name, worksheet)
        if row:
            return row
        
        # Find first empty row after header
        col_a = worksheet.col_values(1)
        next_row = len(col_a) + 1
        
        # Add employee name
        worksheet.update_cell(next_row, 1, employee_name)
        
        return next_row
    
    def submit_hours(self, employee_name: str, date: str, hours: float) -> Dict[str, any]:
        """
        Submit hours worked for an employee on a specific date.
        
        Args:
            employee_name: Employee name
            date: Date in YYYY-MM-DD format
            hours: Hours worked
        
        Returns:
            Dictionary with submission details
        """
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        worksheet = self.get_or_create_month_sheet(date_obj)
        
        # Get or create date column
        col_idx, col_created = self.get_or_create_date_column(date, worksheet)
        
        # Get or create employee row
        row_idx = self.get_or_create_employee_row(employee_name, worksheet)
        
        # Check if cell is empty (safety guard)
        cell_value = worksheet.cell(row_idx, col_idx).value
        if cell_value and cell_value.strip():
            raise Exception(f"Cell already contains data: {cell_value}. Cannot overwrite.")
        
        # Write hours
        worksheet.update_cell(row_idx, col_idx, hours)
        
        return {
            "row": row_idx,
            "column": col_idx,
            "hours": hours,
            "column_created": col_created
        }
    
    def is_month_closed(self, date: str) -> bool:
        """
        Check if a month is closed for submissions.
        Months are closed after the 2nd of the following month.
        
        Args:
            date: Date string in YYYY-MM-DD format
        
        Returns:
            True if month is closed, False otherwise
        """
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        today = datetime.now()
        
        # Calculate the 2nd of the next month after the submission date
        if date_obj.month == 12:
            cutoff_date = datetime(date_obj.year + 1, 1, 2)
        else:
            cutoff_date = datetime(date_obj.year, date_obj.month + 1, 2)
        
        # Month is closed if today is after the cutoff
        return today > cutoff_date
    
    def submit_daily_tips(self, date: str, total_tips: float) -> Dict[str, any]:
        """
        Submit total daily tips and inject formulas.
        
        Args:
            date: Date in YYYY-MM-DD format
            total_tips: Total tips collected
        
        Returns:
            Dictionary with submission details
        """
        date_obj = datetime.strptime(date, "%Y-%m-%d")
        worksheet = self.get_or_create_month_sheet(date_obj)
        
        # Get or create date column
        col_idx, col_created = self.get_or_create_date_column(date, worksheet)
        col_letter = self._col_index_to_letter(col_idx)
        
        # Get all employee rows (all rows with names in column A)
        col_a = worksheet.col_values(1)
        employee_rows = []
        for idx, name in enumerate(col_a, start=1):
            if idx > 1 and name.strip():  # Skip header row
                employee_rows.append(idx)
        
        if not employee_rows:
            raise Exception("No employees found in the sheet")
        
        # Find totals section (3 rows after last employee)
        totals_start_row = max(employee_rows) + 2
        
        # Row positions for totals
        total_tips_row = totals_start_row
        total_hours_row = totals_start_row + 1
        tip_rate_row = totals_start_row + 2
        
        # Add labels if not exist
        if not worksheet.cell(total_tips_row, 1).value:
            worksheet.update_cell(total_tips_row, 1, "Total Tips (T)")
        if not worksheet.cell(total_hours_row, 1).value:
            worksheet.update_cell(total_hours_row, 1, "Total Hours (H)")
        if not worksheet.cell(tip_rate_row, 1).value:
            worksheet.update_cell(tip_rate_row, 1, "Tip Rate (R)")
        
        # Write total tips value
        worksheet.update_cell(total_tips_row, col_idx, total_tips)
        
        # Inject formulas
        self._inject_formulas(worksheet, col_idx, col_letter, employee_rows, 
                             total_tips_row, total_hours_row, tip_rate_row)
        
        return {
            "column": col_idx,
            "total_tips": total_tips,
            "employee_count": len(employee_rows),
            "formulas_injected": True
        }
    
    def _inject_formulas(self, worksheet: gspread.Worksheet, col_idx: int, col_letter: str,
                        employee_rows: List[int], total_tips_row: int, 
                        total_hours_row: int, tip_rate_row: int) -> None:
        """
        Inject formulas for tip calculations.
        
        Args:
            worksheet: Target worksheet
            col_idx: Column index
            col_letter: Column letter (A, B, C, etc.)
            employee_rows: List of employee row numbers
            total_tips_row: Row for total tips
            total_hours_row: Row for total hours
            tip_rate_row: Row for tip rate
        """
        # Formula for Total Hours (H): Sum of all employee hours in this column
        employee_range = ",".join([f"{col_letter}{row}" for row in employee_rows])
        total_hours_formula = f"=SUM({employee_range})"
        worksheet.update_cell(total_hours_row, col_idx, total_hours_formula)
        
        # Formula for Tip Rate (R): T / H
        tip_rate_formula = f"={col_letter}{total_tips_row}/{col_letter}{total_hours_row}"
        worksheet.update_cell(tip_rate_row, col_idx, tip_rate_formula)
        
        # Note: Individual payouts (Pi = hi × R) would be calculated in a separate column
        # or we can add them below the employee hours if needed


# Global service instance
gs_service = GoogleSheetsService()

 