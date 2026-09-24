from fastapi import HTTPException
from supabase import Client
from datetime import datetime, time, timedelta
from app.models.request import (
    AdhocRequestCreate,
    DropoffRequestCreate,
    DropoffRequestUpdate,
    PickupRequestCreate,
    PickupRequestUpdate,
    WeeklyRequestCreate,
)
from app.services.week_service import (
    ADHOC_CUTOFF_TIME,
    NIGHT_SHIFT_HOURS,
    WEEK_DAY_KEYS,
    adhoc_window_status,
    day_key,
    deadline_for_target,
    is_within_request_window,
    next_window_open,
    request_window_open,
    target_service_week,
)

DHAKA_MIN_LAT = 23.55
DHAKA_MAX_LAT = 24.05
DHAKA_MIN_LNG = 90.20
DHAKA_MAX_LNG = 90.60

class RequestService:
    def __init__(self, db: Client):
        self.db = db

    def _fetch_all(self, table: str, select: str, eq_filters: dict) -> list[dict]:
        """Fetch every row matching `eq_filters`, looping past PostgREST's
        default ~1000-row response cap via `.range()`.

        A single `.execute()` silently truncates there — `get_all_pickups`
        and `get_all_dropoffs` used to do exactly that, which made anything
        computed from their result (an admin "Approved" total, or a single
        date's request count once it grew past 1000 rows) quietly wrong with
        no error raised anywhere.
        """
        page_size = 1000
        start = 0
        all_rows: list[dict] = []
        while True:
            query = self.db.table(table).select(select)
            for col, val in eq_filters.items():
                query = query.eq(col, val)
            res = query.order("created_at", desc=True).range(start, start + page_size - 1).execute()
            rows = res.data or []
            all_rows.extend(rows)
            if len(rows) < page_size:
                break
            start += page_size
        return all_rows

    # ── Pickup Requests ─────────────────────────────────────────

    def create_pickup(self, user_id: int, data: PickupRequestCreate):
        employee_id = self._get_employee_id_for_user(user_id)
        self._validate_dhaka_bbox(data.pickup_lat, data.pickup_lng)
        self._ensure_no_duplicate_pickup(employee_id, data.service_date, data.shift_start_time)

        payload = {
            "employee_id": employee_id,
            "zone_id": data.zone_id,
            "pickup_lat": data.pickup_lat,
            "pickup_lng": data.pickup_lng,
            "shift_start_time": self._time_to_str(data.shift_start_time),
            "service_date": data.service_date.isoformat(),
            "request_type": self._pickup_request_type(data.service_date),
            "status": "Pending",
        }
        self._ensure_adhoc_pickup_window(payload["request_type"], data.service_date, data.shift_start_time)
        res = self.db.table("pickup_request").insert(payload).execute()
        return self.get_pickup_by_id(res.data[0]["pickup_id"])

    def get_my_pickups(self, user_id: int, service_date: str = None, status: str = None):
        employee_id = self._get_employee_id_for_user(user_id)
        query = (
            self.db.table("pickup_request")
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("employee_id", employee_id)
        )
        if service_date:
            query = query.eq("service_date", service_date)
        if status:
            query = query.eq("status", status)

        res = query.order("created_at", desc=True).execute()
        return [self._flatten_pickup(r) for r in res.data]

    def get_all_pickups(self, status: str = None, service_date: str = None):
        filters = {}
        if status:
            filters["status"] = status
        if service_date:
            filters["service_date"] = service_date
        rows = self._fetch_all(
            "pickup_request",
            "*, employee(employee_id, users(name)), zone(zone_name)",
            filters,
        )
        return [self._flatten_pickup(r) for r in rows]

    def update_my_pickup(self, pickup_id: int, user_id: int, data: PickupRequestUpdate):
        employee_id = self._get_employee_id_for_user(user_id)
        req = self._get_pickup_or_404(pickup_id, employee_id=employee_id)
        if req["status"] != "Pending":
            raise HTTPException(status_code=409, detail="Only pending pickup requests can be edited")

        next_service_date = data.service_date or req["service_date"]
        next_shift_start_time = data.shift_start_time or req["shift_start_time"]
        next_pickup_lat = data.pickup_lat if data.pickup_lat is not None else req["pickup_lat"]
        next_pickup_lng = data.pickup_lng if data.pickup_lng is not None else req["pickup_lng"]

        self._validate_dhaka_bbox(next_pickup_lat, next_pickup_lng)
        self._ensure_no_duplicate_pickup(
            employee_id,
            next_service_date,
            next_shift_start_time,
            exclude_pickup_id=pickup_id,
        )

        payload = {}
        if data.zone_id is not None:
            payload["zone_id"] = data.zone_id
        if data.pickup_lat is not None:
            payload["pickup_lat"] = data.pickup_lat
        if data.pickup_lng is not None:
            payload["pickup_lng"] = data.pickup_lng
        if data.shift_start_time is not None:
            payload["shift_start_time"] = self._time_to_str(data.shift_start_time)
        if data.service_date is not None:
            payload["service_date"] = data.service_date.isoformat()
            payload["request_type"] = self._pickup_request_type(data.service_date)

        request_type = payload.get("request_type") or req.get("request_type")
        self._ensure_adhoc_pickup_window(request_type, next_service_date, next_shift_start_time)

        if payload:
            self.db.table("pickup_request").update(payload).eq("pickup_id", pickup_id).execute()
        return self.get_pickup_by_id(pickup_id)

    def delete_my_pickup(self, pickup_id: int, user_id: int):
        employee_id = self._get_employee_id_for_user(user_id)
        req = self._get_pickup_or_404(pickup_id, employee_id=employee_id)
        if req["status"] != "Pending":
            raise HTTPException(status_code=409, detail="Only pending pickup requests can be cancelled")

        self.db.table("pickup_request").delete().eq("pickup_id", pickup_id).execute()
        return {"message": "Pickup request cancelled"}

    def approve_pickup(self, pickup_id: int):
        req = self._get_pickup_or_404(pickup_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot approve — request is already '{req['status']}'"
            )
        self.db.table("pickup_request").update({"status": "Approved"}).eq("pickup_id", pickup_id).execute()
        return self.get_pickup_by_id(pickup_id)

    def reject_pickup(self, pickup_id: int):
        req = self._get_pickup_or_404(pickup_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject — request is already '{req['status']}'"
            )
        self.db.table("pickup_request").update({"status": "Rejected"}).eq("pickup_id", pickup_id).execute()
        return self.get_pickup_by_id(pickup_id)

    # ── Dropoff Requests ─────────────────────────────────────────

    def create_dropoff(self, user_id: int, data: DropoffRequestCreate):
        employee_id = self._get_employee_id_for_user(user_id)
        self._validate_dhaka_bbox(data.drop_lat, data.drop_lng)
        self._ensure_no_duplicate_dropoff(employee_id, data.service_date, data.shift_end_time)

        payload = {
            "employee_id": employee_id,
            "zone_id": data.zone_id,
            "drop_lat": data.drop_lat,
            "drop_lng": data.drop_lng,
            "shift_end_time": self._time_to_str(data.shift_end_time),
            "service_date": data.service_date.isoformat(),
            "status": "Pending",
        }
        res = self.db.table("dropoff_request").insert(payload).execute()
        return self.get_dropoff_by_id(res.data[0]["dropoff_id"])

    def get_my_dropoffs(self, user_id: int, service_date: str = None, status: str = None):
        employee_id = self._get_employee_id_for_user(user_id)
        query = (
            self.db.table("dropoff_request")
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("employee_id", employee_id)
        )
        if service_date:
            query = query.eq("service_date", service_date)
        if status:
            query = query.eq("status", status)

        res = query.order("created_at", desc=True).execute()
        return [self._flatten_dropoff(r) for r in res.data]

    def update_my_dropoff(self, dropoff_id: int, user_id: int, data: DropoffRequestUpdate):
        employee_id = self._get_employee_id_for_user(user_id)
        req = self._get_dropoff_or_404(dropoff_id, employee_id=employee_id)
        if req["status"] != "Pending":
            raise HTTPException(status_code=409, detail="Only pending dropoff requests can be edited")

        next_service_date = data.service_date or req["service_date"]
        next_shift_end_time = data.shift_end_time or req["shift_end_time"]
        next_drop_lat = data.drop_lat if data.drop_lat is not None else req["drop_lat"]
        next_drop_lng = data.drop_lng if data.drop_lng is not None else req["drop_lng"]

        self._validate_dhaka_bbox(next_drop_lat, next_drop_lng)
        self._ensure_no_duplicate_dropoff(
            employee_id,
            next_service_date,
            next_shift_end_time,
            exclude_dropoff_id=dropoff_id,
        )

        payload = {}
        if data.zone_id is not None:
            payload["zone_id"] = data.zone_id
        if data.drop_lat is not None:
            payload["drop_lat"] = data.drop_lat
        if data.drop_lng is not None:
            payload["drop_lng"] = data.drop_lng
        if data.shift_end_time is not None:
            payload["shift_end_time"] = self._time_to_str(data.shift_end_time)
        if data.service_date is not None:
            payload["service_date"] = data.service_date.isoformat()

        if payload:
            self.db.table("dropoff_request").update(payload).eq("dropoff_id", dropoff_id).execute()
        return self.get_dropoff_by_id(dropoff_id)

    def delete_my_dropoff(self, dropoff_id: int, user_id: int):
        employee_id = self._get_employee_id_for_user(user_id)
        req = self._get_dropoff_or_404(dropoff_id, employee_id=employee_id)
        if req["status"] != "Pending":
            raise HTTPException(status_code=409, detail="Only pending dropoff requests can be cancelled")

        self.db.table("dropoff_request").delete().eq("dropoff_id", dropoff_id).execute()
        return {"message": "Dropoff request cancelled"}

    def get_all_dropoffs(self, status: str = None, service_date: str = None):
        filters = {}
        if status:
            filters["status"] = status
        if service_date:
            filters["service_date"] = service_date
        rows = self._fetch_all(
            "dropoff_request",
            "*, employee(employee_id, users(name)), zone(zone_name)",
            filters,
        )
        return [self._flatten_dropoff(r) for r in rows]

    def get_dropoff_by_id(self, dropoff_id: int):
        res = (
            self.db.table("dropoff_request")
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("dropoff_id", dropoff_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Dropoff request not found")
        return self._flatten_dropoff(res.data[0])

    def approve_dropoff(self, dropoff_id: int):
        req = self._get_dropoff_or_404(dropoff_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot approve — request is already '{req['status']}'"
            )
        self.db.table("dropoff_request").update({"status": "Approved"}).eq("dropoff_id", dropoff_id).execute()
        return self.get_dropoff_by_id(dropoff_id)

    def reject_dropoff(self, dropoff_id: int):
        req = self._get_dropoff_or_404(dropoff_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject — request is already '{req['status']}'"
            )
        self.db.table("dropoff_request").update({"status": "Rejected"}).eq("dropoff_id", dropoff_id).execute()
        return self.get_dropoff_by_id(dropoff_id)

    # ── Weekly Requests ──────────────────────────────────────────
    #
    # One request per week, submitted Friday/Saturday for the FOLLOWING week's
    # Friday & Saturday. Submitting again updates the same rows (upsert), so
    # only the latest version counts for the routing algorithm.

    def get_current_weekly_request(self, user_id: int):
        employee_id = self._get_employee_id_for_user(user_id)
        now = datetime.now()
        service_days = target_service_week(now.date())
        window_open = is_within_request_window(now)

        def _day_view(service_date):
            pickup = self._get_weekly_row("pickup_request", employee_id, service_date)
            dropoff = self._get_weekly_row("dropoff_request", employee_id, service_date)
            return {
                "date": service_date.isoformat(),
                "pickup": pickup,
                "dropoff": dropoff,
            }

        window = {
            "open": window_open,
            "opens": datetime.combine(request_window_open(now.date()), time(0, 0)).isoformat(),
            "closes": deadline_for_target(now.date()).isoformat(),
            "next_open": next_window_open(now).isoformat(),
            "closed_reason": (
                None
                if window_open
                else "Weekly requests are only accepted on Friday and Saturday, until Saturday 11:59 PM."
            ),
        }

        return {
            "open": window_open,
            "window": window,
            "service_start": service_days[0].isoformat(),
            "service_end": service_days[-1].isoformat(),
            "week": {
                day_key(service_date): _day_view(service_date)
                for service_date in service_days
            },
        }

    def save_weekly_request(self, user_id: int, data: WeeklyRequestCreate):
        employee_id = self._get_employee_id_for_user(user_id)
        now = datetime.now()
        if not is_within_request_window(now):
            raise HTTPException(
                status_code=409,
                detail="Weekly requests are only accepted on Friday and Saturday, until Saturday 11:59 PM",
            )

        service_days = target_service_week(now.date())
        requested = {}
        for key, service_date in zip(WEEK_DAY_KEYS, service_days):
            day = getattr(data, key, None)
            if day is not None:
                self._validate_weekly_day(day)
                requested[service_date] = day
        if not requested:
            raise HTTPException(status_code=422, detail="At least one day must be provided")

        # The submitted form is the full truth for the whole service week: every
        # day listed is written as exactly one fresh Pending pair, and every day
        # left out is cleared. Routing between edits may already have flipped
        # earlier rows to Approved and attached them to routes, so the rewrite
        # detaches the employee from each day's routes and replaces/removes the
        # day's weekly rows whatever their status — otherwise stale Approved
        # ghosts accumulate and My Requests starts listing days the employee no
        # longer requests.
        for service_date in service_days:
            if service_date in requested:
                self._replace_weekly_day(employee_id, service_date, requested[service_date])
            else:
                self._clear_weekly_day(employee_id, service_date)

        return self.get_current_weekly_request(user_id)

    def _validate_weekly_day(self, day):
        self._validate_shift_pair(day.shift_start_time, day.shift_end_time)
        self._validate_dhaka_bbox(day.pickup_lat, day.pickup_lng)
        self._validate_dhaka_bbox(day.drop_lat, day.drop_lng)

    def _validate_shift_pair(self, start, end):
        """Overnight shifts (10 PM → 6 AM) may wrap past midnight, so an end that
        is earlier on the clock than the start is valid; only an equal start/end
        is meaningless."""
        if self._normalize_time(start) == self._normalize_time(end):
            raise HTTPException(
                status_code=422,
                detail="Shift start and end must be different (overnight shifts run into the next day)",
            )
        self._validate_shift_in_night(start)
        self._validate_shift_in_night(end)

    def _validate_shift_in_night(self, t):
        if self._time_to_str(t)[:5] not in NIGHT_SHIFT_HOURS:
            raise HTTPException(
                status_code=422,
                detail="Shift time must be one of: 22:00, 23:00, 00:00, 01:00, 02:00, 03:00, 04:00, 05:00, 06:00",
            )

    def _replace_weekly_day(self, employee_id: int, service_date, day):
        """Rewrite one service day's weekly request as a single fresh Pending pair.

        The day may already hold rows routed by an earlier run (Approved) or be
        a leftover from an older submission. Whatever their status, those rows
        are removed and the employee is detached from the day's routes (clearing
        their driver manifest) before the new Pending pair is written. After this
        call exactly one weekly pickup + dropoff row exists for (employee, date).
        The day's ad-hoc request, if any, is untouched.
        """
        self._detach_employee_routes(employee_id, service_date)
        self._delete_day_weekly_rows(employee_id, service_date)
        self._insert_weekly_pair(employee_id, service_date, day)

    def _clear_weekly_day(self, employee_id: int, service_date):
        """Remove a service day's weekly request entirely (rows of any status)
        and detach the employee from the day's routes. Used for days the employee
        left out of the submitted week."""
        self._detach_employee_routes(employee_id, service_date)
        self._delete_day_weekly_rows(employee_id, service_date)

    def _delete_day_weekly_rows(self, employee_id: int, service_date):
        """Delete every weekly (non-ad-hoc) pickup and dropoff row for one day.

        Pickups are typed, so only ``request_type == 'Regular'`` rows go.
        Dropoffs carry no request_type; a day's ad-hoc dropoff (created at/after
        the day's ad-hoc pickup) is preserved by removing only dropoffs older
        than the earliest ad-hoc pickup, mirroring ``_delete_prior_adhoc``.
        """
        iso = self._date_to_str(service_date)
        self.db.table("pickup_request").delete() \
            .eq("employee_id", employee_id) \
            .eq("service_date", iso) \
            .eq("request_type", "Regular").execute()
        adhoc = (
            self.db.table("pickup_request")
            .select("created_at")
            .eq("employee_id", employee_id)
            .eq("service_date", iso)
            .eq("request_type", "Ad-hoc")
            .execute()
        ).data or []
        query = (
            self.db.table("dropoff_request")
            .delete()
            .eq("employee_id", employee_id)
            .eq("service_date", iso)
        )
        if adhoc:
            query = query.lt("created_at", min(r["created_at"] for r in adhoc))
        query.execute()

    def _insert_weekly_pair(self, employee_id: int, service_date, day):
        self.db.table("pickup_request").insert({
            "employee_id": employee_id,
            "pickup_lat": day.pickup_lat,
            "pickup_lng": day.pickup_lng,
            "shift_start_time": self._time_to_str(day.shift_start_time),
            "service_date": self._date_to_str(service_date),
            "request_type": "Regular",
            "status": "Pending",
        }).execute()
        self.db.table("dropoff_request").insert({
            "employee_id": employee_id,
            "drop_lat": day.drop_lat,
            "drop_lng": day.drop_lng,
            "shift_end_time": self._time_to_str(day.shift_end_time),
            "service_date": self._date_to_str(service_date),
            "status": "Pending",
        }).execute()

    def _get_weekly_row(self, table: str, employee_id: int, service_date):
        """Most recent weekly row (pickup or dropoff) for a target date.

        Returns pending OR approved rows — the weekly view must keep showing the
        employee's request after routing flips it to Approved, so the form stays
        in edit mode with the submitted details.
        """
        id_col = "pickup_id" if table == "pickup_request" else "dropoff_id"
        query = (
            self.db.table(table)
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("employee_id", employee_id)
            .eq("service_date", self._date_to_str(service_date))
            .in_("status", ("Pending", "Approved"))
        )
        if table == "pickup_request":
            query = query.eq("request_type", "Regular")
        res = query.order("created_at", desc=True).limit(1).execute()
        if not res.data:
            return None
        return self._flatten_pickup(res.data[0]) if table == "pickup_request" else self._flatten_dropoff(res.data[0])

    # ── Ad-hoc Requests ─────────────────────────────────────────
    #
    # Same-day requests: available only ON the service day, before 7 PM (three
    # hours before the 10 PM overnight shift). An ad-hoc request changes that
    # day's shift + pickup/dropoff. It does NOT delete the day's weekly
    # request — both rows are kept so the weekly one stays as a fallback if the
    # ad-hoc is rejected. Routing counts only the newest row per (employee,
    # date) (see routing_service), which is the ad-hoc one — ad-hoc rows are
    # always inserted after the weekly rows.

    def get_current_adhoc(self, user_id: int):
        employee_id = self._get_employee_id_for_user(user_id)
        status = adhoc_window_status()
        return {
            "open": status["open"],
            "service_date": status["service_date"],
            "cutoff": status["cutoff"],
            "reason": status["reason"],
            "existing": {
                "pickup": self._latest_day_row("pickup_request", employee_id, datetime.now().date()),
                "dropoff": self._latest_day_row("dropoff_request", employee_id, datetime.now().date()),
            },
        }

    def save_adhoc(self, user_id: int, data: AdhocRequestCreate):
        employee_id = self._get_employee_id_for_user(user_id)
        now = datetime.now()

        status = adhoc_window_status(now)
        if not status["open"]:
            raise HTTPException(status_code=409, detail=status["reason"])

        self._validate_shift_pair(data.shift_start_time, data.shift_end_time)
        self._validate_dhaka_bbox(data.pickup_lat, data.pickup_lng)
        self._validate_dhaka_bbox(data.drop_lat, data.drop_lng)

        service_date = now.date()
        # Remove the employee from today's existing route stops so their driver
        # manifest is cleared before the nightly re-run rebuilds the route.
        self._detach_employee_routes(employee_id, service_date)
        # Turn today's already-routed (weekly) rows back into Pending fallbacks
        # so the newest-wins rule routes the ad-hoc rows instead.
        self._reset_day_requests_to_pending(employee_id, service_date)
        # Replace any earlier ad-hoc pair for today (there is only ever one).
        self._delete_prior_adhoc(employee_id, service_date)

        self.db.table("pickup_request").insert({
            "employee_id": employee_id,
            "pickup_lat": data.pickup_lat,
            "pickup_lng": data.pickup_lng,
            "shift_start_time": self._time_to_str(data.shift_start_time),
            "service_date": self._date_to_str(service_date),
            "request_type": "Ad-hoc",
            "status": "Pending",
        }).execute()
        self.db.table("dropoff_request").insert({
            "employee_id": employee_id,
            "drop_lat": data.drop_lat,
            "drop_lng": data.drop_lng,
            "shift_end_time": self._time_to_str(data.shift_end_time),
            "service_date": self._date_to_str(service_date),
            "status": "Pending",
        }).execute()

        return self.get_current_adhoc(user_id)

    def _latest_day_row(self, table: str, employee_id: int, service_date):
        """Newest Pending/Approved pickup or dropoff for (employee, date).

        For a day with an ad-hoc request this is the ad-hoc row (created today);
        without one it is the weekly row (created in the request window)."""
        query = (
            self.db.table(table)
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("employee_id", employee_id)
            .eq("service_date", self._date_to_str(service_date))
            .in_("status", ("Pending", "Approved"))
        )
        res = query.order("created_at", desc=True).limit(1).execute()
        if not res.data:
            return None
        return self._flatten_pickup(res.data[0]) if table == "pickup_request" else self._flatten_dropoff(res.data[0])

    def _detach_employee_routes(self, employee_id: int, service_date):
        """Delete the employee's stop_passenger rows on today's routes so their
        driver manifest no longer references them."""
        routes = self.db.table("route").select("route_id").eq("service_date", self._date_to_str(service_date)).execute()
        route_ids = [r["route_id"] for r in (routes.data or [])]
        if not route_ids:
            return
        stops = self.db.table("route_stop").select("stop_id").in_("route_id", route_ids).execute()
        stop_ids = [s["stop_id"] for s in (stops.data or [])]
        if not stop_ids:
            return
        self.db.table("stop_passenger").delete().eq("employee_id", employee_id).in_("stop_id", stop_ids).execute()

    def _reset_day_requests_to_pending(self, employee_id: int, service_date):
        """Detach the day's already-routed rows (the weekly request) from their
        route and leave them Pending as a fallback, so the newest-wins routing
        rule routes the ad-hoc rows instead."""
        for table in ("pickup_request", "dropoff_request"):
            self.db.table(table).update({"status": "Pending", "route_id": None}) \
                .eq("employee_id", employee_id) \
                .eq("service_date", self._date_to_str(service_date)) \
                .eq("status", "Approved").execute()

    def _delete_prior_adhoc(self, employee_id: int, service_date):
        """Remove any previous ad-hoc pair for the day, leaving exactly one
        ad-hoc request. The weekly request is never touched. Dropoffs carry no
        request_type, so the ad-hoc dropoff(s) are found by created_at — they
        are always newer than the weekly rows for the same date."""
        prior = (
            self.db.table("pickup_request")
            .select("created_at")
            .eq("employee_id", employee_id)
            .eq("service_date", self._date_to_str(service_date))
            .eq("request_type", "Ad-hoc")
            .execute()
        )
        rows = prior.data or []
        if not rows:
            return
        min_created = min(r["created_at"] for r in rows)
        self.db.table("dropoff_request").delete() \
            .eq("employee_id", employee_id) \
            .eq("service_date", self._date_to_str(service_date)) \
            .in_("status", ("Pending", "Approved")) \
            .gte("created_at", min_created).execute()
        self.db.table("pickup_request").delete() \
            .eq("employee_id", employee_id) \
            .eq("service_date", self._date_to_str(service_date)) \
            .eq("request_type", "Ad-hoc").execute()

    # ── Helpers ─────────────────────────────────────────────────

    def get_pickup_by_id(self, pickup_id: int):
        res = (
            self.db.table("pickup_request")
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("pickup_id", pickup_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Pickup request not found")
        return self._flatten_pickup(res.data[0])

    def _get_pickup_or_404(self, pickup_id: int, employee_id: int = None):
        query = (
            self.db.table("pickup_request")
            .select("pickup_id, employee_id, status, pickup_lat, pickup_lng, shift_start_time, service_date, request_type")
            .eq("pickup_id", pickup_id)
        )
        if employee_id is not None:
            query = query.eq("employee_id", employee_id)

        res = query.limit(1).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Pickup request not found")
        return res.data[0]

    def _get_dropoff_or_404(self, dropoff_id: int, employee_id: int = None):
        query = (
            self.db.table("dropoff_request")
            .select("dropoff_id, employee_id, status, drop_lat, drop_lng, shift_end_time, service_date")
            .eq("dropoff_id", dropoff_id)
        )
        if employee_id is not None:
            query = query.eq("employee_id", employee_id)

        res = query.limit(1).execute()
        if not res.data:
            raise HTTPException(status_code=404, detail="Dropoff request not found")
        return res.data[0]

    def _get_employee_id_for_user(self, user_id: int):
        res = (
            self.db.table("employee")
            .select("employee_id")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=403, detail="Employee account required")
        return res.data[0]["employee_id"]

    def _validate_dhaka_bbox(self, lat: float, lng: float):
        if not (DHAKA_MIN_LAT <= lat <= DHAKA_MAX_LAT and DHAKA_MIN_LNG <= lng <= DHAKA_MAX_LNG):
            raise HTTPException(status_code=422, detail="Location must be inside Dhaka service area")

    def _pickup_request_type(self, service_date):
        service_date = self._normalize_date(service_date)
        if service_date.weekday() >= 5:
            return "Ad-hoc"

        deadline = datetime.combine(service_date - timedelta(days=1), time(18, 0))
        return "Regular" if datetime.now() <= deadline else "Ad-hoc"

    def _ensure_adhoc_pickup_window(self, request_type: str, service_date, shift_start_time):
        if request_type != "Ad-hoc":
            return

        shift_datetime = datetime.combine(
            self._normalize_date(service_date),
            self._normalize_time(shift_start_time),
        )
        if shift_datetime - datetime.now() < timedelta(hours=2):
            raise HTTPException(
                status_code=409,
                detail="Ad-hoc pickup requests must be submitted at least 2 hours before shift start",
            )

    def _ensure_no_duplicate_pickup(
        self,
        employee_id: int,
        service_date,
        shift_start_time,
        exclude_pickup_id: int = None,
    ):
        query = (
            self.db.table("pickup_request")
            .select("pickup_id")
            .eq("employee_id", employee_id)
            .eq("service_date", self._date_to_str(service_date))
            .eq("shift_start_time", self._time_to_str(shift_start_time))
            .in_("status", ["Pending", "Approved"])
        )
        if exclude_pickup_id is not None:
            query = query.neq("pickup_id", exclude_pickup_id)

        if query.limit(1).execute().data:
            raise HTTPException(status_code=409, detail="Duplicate pickup request already exists")

    def _ensure_no_duplicate_dropoff(
        self,
        employee_id: int,
        service_date,
        shift_end_time,
        exclude_dropoff_id: int = None,
    ):
        query = (
            self.db.table("dropoff_request")
            .select("dropoff_id")
            .eq("employee_id", employee_id)
            .eq("service_date", self._date_to_str(service_date))
            .eq("shift_end_time", self._time_to_str(shift_end_time))
            .in_("status", ["Pending", "Approved"])
        )
        if exclude_dropoff_id is not None:
            query = query.neq("dropoff_id", exclude_dropoff_id)

        if query.limit(1).execute().data:
            raise HTTPException(status_code=409, detail="Duplicate dropoff request already exists")

    def _normalize_date(self, value):
        if hasattr(value, "isoformat") and not isinstance(value, str):
            return value
        return datetime.strptime(value, "%Y-%m-%d").date()

    def _normalize_time(self, value):
        if hasattr(value, "isoformat") and not isinstance(value, str):
            return value
        return time.fromisoformat(value)

    def _date_to_str(self, value):
        return self._normalize_date(value).isoformat()

    def _time_to_str(self, value):
        return self._normalize_time(value).isoformat()

    def _flatten_dropoff(self, row: dict) -> dict:
        employee = row.get("employee", None) or {}
        users = employee.get("users", None) or {}
        zone = row.get("zone", None) or {}
        return {
            **{k: v for k, v in row.items() if k not in {"employee", "zone"}},
            "employee_name": users.get("name"),
            "zone_name": zone.get("zone_name"),
        }

    def _flatten_pickup(self, row: dict) -> dict:
        employee = row.get("employee", None) or {}
        users = employee.get("users", None) or {}
        zone = row.get("zone", None) or {}
        return {
            **{k: v for k, v in row.items() if k not in {"employee", "zone"}},
            "employee_name": users.get("name"),
            "zone_name": zone.get("zone_name"),
        }
