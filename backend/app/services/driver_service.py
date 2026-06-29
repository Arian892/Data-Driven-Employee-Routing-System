from fastapi import HTTPException
from supabase import Client
from app.models.driver import DriverCreate, DriverUpdate

class DriverService:
    def __init__(self, db: Client):
        self.db = db

    def get_all(self):
        """Join driver → users to get full driver info."""
        res = (
            self.db.table("driver")
            .select("driver_id, user_id, license_no, status, users(name, email, phone, status)")
            .execute()
        )
        return [self._flatten(d) for d in res.data]

    def get_by_id(self, driver_id: int):
        res = (
            self.db.table("driver")
            .select("driver_id, user_id, license_no, status, users(name, email, phone, status)")
            .eq("driver_id", driver_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Driver not found")
        return self._flatten(res.data)

    def create(self, data: DriverCreate):
        # 1. Check email uniqueness
        existing = (
            self.db.table("users")
            .select("user_id")
            .eq("email", data.email)
            .execute()
        )
        if existing.data:
            raise HTTPException(status_code=409, detail="Email already in use")

        # 2. Insert into users
        user_res = (
            self.db.table("users")
            .insert({
                "name": data.name,
                "email": data.email,
                "phone": data.phone,
                "password_hash": data.password,   # stored as-is
                "role": "Driver",
                "status": "Active"
            })
            .execute()
        )
        user_id = user_res.data[0]["user_id"]

        # 3. Insert into driver
        driver_res = (
            self.db.table("driver")
            .insert({
                "user_id": user_id,
                "license_no": data.license_no,
                "status": data.status
            })
            .execute()
        )
        return self.get_by_id(driver_res.data[0]["driver_id"])

    def update(self, driver_id: int, data: DriverUpdate):
        # Verify driver exists
        existing = self.get_by_id(driver_id)

        # Update users table if name/phone provided
        user_payload = {}
        if data.name is not None:
            user_payload["name"] = data.name
        if data.phone is not None:
            user_payload["phone"] = data.phone

        if user_payload:
            self.db.table("users").update(user_payload).eq("user_id", existing["user_id"]).execute()

        # Update driver table if license/status provided
        driver_payload = {}
        if data.license_no is not None:
            driver_payload["license_no"] = data.license_no
        if data.status is not None:
            driver_payload["status"] = data.status

        if driver_payload:
            self.db.table("driver").update(driver_payload).eq("driver_id", driver_id).execute()

        return self.get_by_id(driver_id)

    def delete(self, driver_id: int):
        existing = self.get_by_id(driver_id)   # 404 if not found

        # Check if driver is currently assigned to a vehicle
        vehicle_check = (
            self.db.table("vehicle")
            .select("vehicle_id, plate_no")
            .eq("driver_id", driver_id)
            .execute()
        )
        if vehicle_check.data:
            plate = vehicle_check.data[0]["plate_no"]
            raise HTTPException(
                status_code=409,
                detail=f"Driver is assigned to vehicle {plate}. Unassign first."
            )

        self.db.table("driver").delete().eq("driver_id", driver_id).execute()
        self.db.table("users").update({"status": "Inactive"}).eq("user_id", existing["user_id"]).execute()
        return {"message": "Driver deleted and user deactivated"}

    def _flatten(self, row: dict) -> dict:
        """Merge nested users dict into flat response."""
        user = row.pop("users", {}) or {}
        return {
            "driver_id": row["driver_id"],
            "user_id": row["user_id"],
            "license_no": row["license_no"],
            "status": row["status"],
            "name": user.get("name"),
            "email": user.get("email"),
            "phone": user.get("phone"),
            "user_status": user.get("status"),
        }