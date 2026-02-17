import io
from datetime import date, time

from django.test import SimpleTestCase

from scheduling.schedule_document_service import parse_schedule_document


class ScheduleDocumentServiceTests(SimpleTestCase):
    def _make_excel(self, rows):
        import openpyxl

        wb = openpyxl.Workbook()
        ws = wb.active
        for r in rows:
            ws.append(r)
        buf = io.BytesIO()
        wb.save(buf)
        wb.close()
        return buf.getvalue()

    def test_parses_csv_with_common_headers(self):
        csv_bytes = (
            "Date,Employee,Role,Start,End,Department\n"
            "2026-02-17,Emily Chen,Waiter,09:00,17:00,FOH\n"
        ).encode("utf-8")
        out = parse_schedule_document(csv_bytes, filename="week1.csv", content_type="text/csv")
        self.assertFalse(out.get("error"))
        self.assertEqual(out["template_name"], "week1")
        self.assertEqual(len(out["shifts"]), 1)
        s = out["shifts"][0]
        self.assertEqual(s["employee_name"], "Emily Chen")
        self.assertEqual(s["role"], "WAITER")
        self.assertEqual(s["start_time"], "09:00")
        self.assertEqual(s["end_time"], "17:00")
        self.assertEqual(s["department"], "FOH")
        self.assertEqual(s["day_of_week"], 1)  # 2026-02-17 is Tuesday

    def test_header_row_detection_skips_title_row_excel(self):
        xlsx = self._make_excel(
            [
                ["My Restaurant Schedule (Week of Feb 17)"],
                ["Shift Date", "Full Name", "Position", "Start Time", "End Time"],
                ["02/17/2026", "Sam", "Chef", "9am", "5pm"],
            ]
        )
        out = parse_schedule_document(xlsx, filename="import.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertFalse(out.get("error"))
        self.assertEqual(len(out["shifts"]), 1)
        s = out["shifts"][0]
        self.assertEqual(s["employee_name"], "Sam")
        self.assertEqual(s["role"], "CHEF")
        self.assertEqual(s["start_time"], "09:00")
        self.assertEqual(s["end_time"], "17:00")

    def test_parses_mm_dd_yyyy_when_day_part_gt_12(self):
        csv_bytes = (
            "Date,Employee,Role,Start,End\n"
            "02/17/2026,Alex,Waiter,09:00,17:00\n"
        ).encode("utf-8")
        out = parse_schedule_document(csv_bytes, filename="us.csv", content_type="text/csv")
        self.assertFalse(out.get("error"))
        self.assertEqual(out["shifts"][0]["day_of_week"], 1)  # Tuesday

    def test_parses_excel_native_date_and_time_cells(self):
        # openpyxl returns date/time objects for these cells
        xlsx = self._make_excel(
            [
                ["Date", "Employee", "Role", "Start", "End"],
                [date(2026, 2, 16), "Nora", "Manager", time(9, 30), time(18, 0)],
            ]
        )
        out = parse_schedule_document(xlsx, filename="native.xlsx", content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertFalse(out.get("error"))
        s = out["shifts"][0]
        self.assertEqual(s["day_of_week"], 0)  # Monday
        self.assertEqual(s["start_time"], "09:30")
        self.assertEqual(s["end_time"], "18:00")

    def test_parses_grid_schedule_with_day_columns(self):
        csv_bytes = (
            "Name,Role,Mon,Tue,Wed,Thu,Fri\n"
            "Emily Chen,Waiter,9am-5pm,OFF,10:00-14:00,,\n"
        ).encode("utf-8")
        out = parse_schedule_document(csv_bytes, filename="grid.csv", content_type="text/csv")
        self.assertFalse(out.get("error"))
        # Two shifts: Mon and Wed
        self.assertEqual(len(out["shifts"]), 2)
        days = sorted([s["day_of_week"] for s in out["shifts"]])
        self.assertEqual(days, [0, 2])  # Mon=0, Wed=2
        mon = [s for s in out["shifts"] if s["day_of_week"] == 0][0]
        self.assertEqual(mon["employee_name"], "Emily Chen")
        self.assertEqual(mon["start_time"], "09:00")
        self.assertEqual(mon["end_time"], "17:00")

