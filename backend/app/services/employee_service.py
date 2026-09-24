from fastapi import HTTPException, status
from supabase import Client

from app.models.employee import EmployeeCreate, EmployeeProfileUpdate


class EmployeeService:
    def __init__(self, db: Client):
        self.db = db

    def get_all(self) -> list[dict]:
        # Looped via `.range()` rather than a single `.execute()` — PostgREST
        # silently truncates an unbounded select at ~1000 rows, and the
        # employee table (940+ rows, growing) is already close to that.
        select = "employee_id, user_id, home_lat, home_lng, is_active, users(name, email, phone, role, status)"
        page_size = 1000
        start = 0
        rows: list[dict] = []
        while True:
            res = self.db.table("employee").select(select).range(start, start + page_size - 1).execute()
            batch = res.data or []
            rows.extend(batch)
            if len(batch) < page_size:
                break
            start += page_size
        return [self._flatten_employee(row) for row in rows]

    def _get_employee_or_404(self, user_id: int) -> dict:
        res = (
            self.db.table("employee")
            .select("employee_id, user_id, home_lat, home_lng, is_active")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Employee record not found")
        return res.data[0]

    def _flatten_employee(self, row: dict) -> dict:
        user = row.get("users") or {}
        return {
            "user_id": row["user_id"],
            "employee_id": row["employee_id"],
            "name": user.get("name"),
            "email": user.get("email"),
            "phone": user.get("phone"),
            "home_lat": row.get("home_lat"),
            "home_lng": row.get("home_lng"),
            "role": user.get("role", "Employee"),
            "status": user.get("status", "Active"),
            "is_active": row.get("is_active", True),
        }

    def _null_schedule(self, service_date: str) -> dict:
        return {
            "service_date": service_date,
            "pickup": None,
            "dropoff": None,
            "route_id": None,
            "route_type": None,
            "shift_time": None,
            "route_geometry": None,
            "stop": None,
            "driver": None,
            "vehicle": None,
            "routing_done": False,
        }

    # --- Admin account management -------------------------------------------

    def create_employee(self, data: EmployeeCreate) -> dict:
        # 1. Email must be unique (same check as DriverService.create)
        existing = (
            self.db.table("users")
            .select("user_id")
            .eq("email", data.email.strip())
            .execute()
        )
        if existing.data:
            raise HTTPException(status_code=409, detail="Email already in use")

        # 2. Insert the login account
        user_res = (
            self.db.table("users")
            .insert({
                "name": data.name.strip(),
                "email": data.email.strip(),
                "phone": data.phone,
                "password_hash": data.password,  # stored as-is (plaintext, per project model)
                "role": "Employee",
                "status": "Active",
            })
            .execute()
        )
        user_id = user_res.data[0]["user_id"]

        # 3. Insert the employee profile row
        self.db.table("employee").insert({
            "user_id": user_id,
            "is_active": True,
        }).execute()

        return self.get_profile(user_id)

    def reset_password(self, user_id: int, new_password: str) -> dict:
        # Verifies the target is an employee; never reads or returns the
        # existing hash — the admin can only overwrite it.
        self._get_employee_or_404(user_id)
        self.db.table("users").update({"password_hash": new_password}).eq(
            "user_id", user_id
        ).execute()
        return {"message": "Password reset"}

    def deactivate(self, user_id: int) -> dict:
        """Soft delete: the employee keeps their request/routing history but
        can no longer log in. (Hard-deleting would break FK references from
        pickup_request/dropoff_request/stop_passenger.)"""
        self._get_employee_or_404(user_id)
        self.db.table("users").update({"status": "Inactive"}).eq(
            "user_id", user_id
        ).execute()
        self.db.table("employee").update({"is_active": False}).eq(
            "user_id", user_id
        ).execute()
        return {"message": "Employee deactivated"}

    # --- Employee self-service ----------------------------------------------

    def change_password(self, user_id: int, current_password: str, new_password: str) -> dict:
        res = (
            self.db.table("users")
            .select("password_hash")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="User not found")

        if current_password != res.data[0]["password_hash"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Current password is incorrect",
            )

        self.db.table("users").update({"password_hash": new_password}).eq(
            "user_id", user_id
        ).execute()
        return {"message": "Password updated"}

    def get_profile(self, user_id: int) -> dict:
        user_res = (
            self.db.table("users")
            .select("user_id, name, email, phone, role, status")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        if not user_res.data:
            raise HTTPException(status_code=404, detail="User not found")
        user = user_res.data[0]

        emp = self._get_employee_or_404(user_id)

        return {
            "user_id": user_id,
            "employee_id": emp["employee_id"],
            "name": user["name"],
            "email": user["email"],
            "phone": user.get("phone"),
            "home_lat": emp.get("home_lat"),
            "home_lng": emp.get("home_lng"),
            "role": user["role"],
            "status": user["status"],
            "is_active": emp.get("is_active", True),
        }

    def update_profile(self, user_id: int, data: EmployeeProfileUpdate) -> dict:
        emp = self._get_employee_or_404(user_id)
        employee_id = emp["employee_id"]

        user_payload: dict = {}
        if data.name is not None:
            user_payload["name"] = data.name
        if data.phone is not None:
            user_payload["phone"] = data.phone
        if user_payload:
            self.db.table("users").update(user_payload).eq("user_id", user_id).execute()

        emp_payload: dict = {}
        if data.home_lat is not None:
            emp_payload["home_lat"] = data.home_lat
        if data.home_lng is not None:
            emp_payload["home_lng"] = data.home_lng
        if emp_payload:
            self.db.table("employee").update(emp_payload).eq(
                "employee_id", employee_id
            ).execute()

        return self.get_profile(user_id)

    def get_schedule(self, user_id: int, service_date: str) -> dict:
        emp = self._get_employee_or_404(user_id)
        employee_id = emp["employee_id"]

        # 1. Get stop IDs assigned to this employee
        sp_res = (
            self.db.table("stop_passenger")
            .select("stop_id")
            .eq("employee_id", employee_id)
            .execute()
        )
        if not sp_res.data:
            return self._null_schedule(service_date)

        stop_ids = [r["stop_id"] for r in sp_res.data]

        # 2. Get route_stops for those IDs
        rs_res = (
            self.db.table("route_stop")
            .select(
                "stop_id, route_id, latitude, longitude, sequence_order, arrival_time, "
                # the solver names every stop; without these the employee sees a
                # bare lat/lng where "Mazar Road Bus Stop" belongs
                "departure_time, stop_name, is_adhoc, is_shared"
            )
            .in_("stop_id", stop_ids)
            .execute()
        )
        if not rs_res.data:
            return self._null_schedule(service_date)

        route_ids = list({r["route_id"] for r in rs_res.data})

        # 3. Routes on this date. There are normally TWO — the pickup that brings
        #    the employee in and the dropoff that takes them home — because
        #    migration 003 puts both on the same service_date (Case C reuses the
        #    pickup's vehicle, so the two halves have to share a night). Taking
        #    route_res.data[0] here would show one leg and silently hide the other.
        route_res = (
            self.db.table("route")
            .select("route_id, route_type, service_date, shift_time, "
                    "total_distance_km, total_travel_time_min, route_geometry")
            .in_("route_id", route_ids)
            .eq("service_date", service_date)
            .execute()
        )
        if not route_res.data:
            return self._null_schedule(service_date)

        routes_by_id = {r["route_id"]: r for r in route_res.data}

        # 4. Assignments, drivers and vehicles for every leg at once — one call
        #    each rather than two per leg.
        ra_rows = (
            self.db.table("route_assignment")
            .select("assignment_id, route_id, driver_id, vehicle_id")
            .in_("route_id", list(routes_by_id))
            .execute()
            .data
        ) or []
        assignment_by_route: dict = {}
        for ra in ra_rows:
            assignment_by_route.setdefault(ra["route_id"], ra)

        driver_ids = {ra["driver_id"] for ra in ra_rows if ra.get("driver_id")}
        vehicle_ids = {ra["vehicle_id"] for ra in ra_rows if ra.get("vehicle_id")}

        drivers_by_id: dict = {}
        if driver_ids:
            d_rows = (
                self.db.table("driver")
                .select("driver_id, users(name, phone)")
                .in_("driver_id", list(driver_ids))
                .execute()
                .data
            ) or []
            for d in d_rows:
                u = d.get("users") or {}
                drivers_by_id[d["driver_id"]] = {
                    "driver_id": d["driver_id"],
                    "name": u.get("name"),
                    "phone": u.get("phone"),
                }

        vehicles_by_id: dict = {}
        if vehicle_ids:
            v_rows = (
                self.db.table("vehicle")
                .select("vehicle_id, plate_no, capacity")
                .in_("vehicle_id", list(vehicle_ids))
                .execute()
                .data
            ) or []
            for v in v_rows:
                vehicles_by_id[v["vehicle_id"]] = {
                    "vehicle_id": v["vehicle_id"],
                    "plate_no": v.get("plate_no"),
                    "capacity": v.get("capacity"),
                }

        # 4b. Every stop on each of these routes — every colleague sharing the
        #    vehicle, not just this employee's own row from step 2 — so the map
        #    can show the full sequence (1..N), not just one point.
        all_stops_rows = (
            self.db.table("route_stop")
            .select(
                "stop_id, route_id, latitude, longitude, sequence_order, "
                "arrival_time, departure_time, stop_name, is_adhoc, is_shared"
            )
            .in_("route_id", list(routes_by_id))
            .order("sequence_order")
            .execute()
            .data
        ) or []
        stops_by_route: dict = {}
        for row in all_stops_rows:
            stops_by_route.setdefault(row["route_id"], []).append(row)

        # 5. One leg per route, keyed by type. route.route_type is CHECK-constrained
        #    to 'pickup'/'dropoff', but normalise anyway so a stray case can't drop
        #    a leg on the floor.
        legs: dict = {}
        for route_id, route in routes_by_id.items():
            matched_stop = next((r for r in rs_res.data if r["route_id"] == route_id), None)
            if not matched_stop:
                continue
            key = (route.get("route_type") or "").strip().lower()
            if key not in ("pickup", "dropoff") or key in legs:
                continue
            ra = assignment_by_route.get(route_id) or {}
            legs[key] = {
                "route_id": route_id,
                "route_type": key,
                "shift_time": route.get("shift_time"),
                "route_geometry": route.get("route_geometry"),
                "stop": {
                    "stop_id": matched_stop["stop_id"],
                    "sequence_order": matched_stop.get("sequence_order"),
                    "latitude": matched_stop.get("latitude"),
                    "longitude": matched_stop.get("longitude"),
                    "arrival_time": matched_stop.get("arrival_time"),
                    "departure_time": matched_stop.get("departure_time"),
                    "stop_name": matched_stop.get("stop_name"),
                    "is_adhoc": matched_stop.get("is_adhoc"),
                    "is_shared": matched_stop.get("is_shared"),
                },
                "stops": [
                    {
                        "stop_id": s["stop_id"],
                        "sequence_order": s.get("sequence_order"),
                        "latitude": s.get("latitude"),
                        "longitude": s.get("longitude"),
                        "arrival_time": s.get("arrival_time"),
                        "departure_time": s.get("departure_time"),
                        "stop_name": s.get("stop_name"),
                        "is_adhoc": s.get("is_adhoc"),
                        "is_shared": s.get("is_shared"),
                        "is_mine": s["stop_id"] == matched_stop["stop_id"],
                    }
                    for s in stops_by_route.get(route_id, [])
                ],
                "driver": drivers_by_id.get(ra.get("driver_id")),
                "vehicle": vehicles_by_id.get(ra.get("vehicle_id")),
            }

        if not legs:
            return self._null_schedule(service_date)

        # The flat fields mirror the pickup leg (the dropoff, on a dropoff-only
        # day) so consumers written against the earlier single-leg shape keep
        # working unchanged.
        primary = legs.get("pickup") or legs["dropoff"]

        return {
            "service_date": service_date,
            "pickup": legs.get("pickup"),
            "dropoff": legs.get("dropoff"),
            "route_id": primary["route_id"],
            "route_type": primary["route_type"],
            "shift_time": primary["shift_time"],
            "route_geometry": primary["route_geometry"],
            "stop": primary["stop"],
            "driver": primary["driver"],
            "vehicle": primary["vehicle"],
            "routing_done": True,
        }
