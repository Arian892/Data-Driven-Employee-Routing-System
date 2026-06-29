from fastapi import HTTPException
from supabase import Client
from app.models.vehicle import VehicleCreate, VehicleUpdate

class VehicleService:
    def __init__(self, db: Client):
        self.db = db

    def get_all(self):
        res = (
            self.db.table("vehicle")
            .select("*, driver(driver_id, license_no, users(name))")
            .execute()
        )
        return [self._flatten(v) for v in res.data]

    def get_by_id(self, vehicle_id: int):
        res = (
            self.db.table("vehicle")
            .select("*, driver(driver_id, license_no, users(name))")
            .eq("vehicle_id", vehicle_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Vehicle not found")
        return self._flatten(res.data)

    def create(self, data: VehicleCreate):
        # Check plate uniqueness
        existing = (
            self.db.table("vehicle")
            .select("vehicle_id")
            .eq("plate_no", data.plate_no)
            .execute()
        )
        if existing.data:
            raise HTTPException(status_code=409, detail="Plate number already exists")

        if data.driver_id:
            self._validate_driver(data.driver_id)

        res = (
            self.db.table("vehicle")
            .insert(data.model_dump())
            .execute()
        )
        return self.get_by_id(res.data[0]["vehicle_id"])

    def update(self, vehicle_id: int, data: VehicleUpdate):
        self.get_by_id(vehicle_id)  # 404 check

        payload = {k: v for k, v in data.model_dump().items() if v is not None}

        if "driver_id" in payload:
            self._validate_driver(payload["driver_id"])

        self.db.table("vehicle").update(payload).eq("vehicle_id", vehicle_id).execute()
        return self.get_by_id(vehicle_id)

    def delete(self, vehicle_id: int):
        self.get_by_id(vehicle_id)  # 404 check
        self.db.table("vehicle").delete().eq("vehicle_id", vehicle_id).execute()
        return {"message": "Vehicle deleted successfully"}

    def _validate_driver(self, driver_id: int):
        """Ensure driver exists and is Available."""
        res = (
            self.db.table("driver")
            .select("driver_id, status")
            .eq("driver_id", driver_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail=f"Driver {driver_id} not found")
        if res.data["status"] != "Available":
            raise HTTPException(
                status_code=409,
                detail=f"Driver {driver_id} is not Available (current status: {res.data['status']})"
            )

    def _flatten(self, row: dict) -> dict:
        driver = row.pop("driver", None) or {}
        users = driver.pop("users", None) or {}
        return {
            "vehicle_id": row["vehicle_id"],
            "plate_no": row["plate_no"],
            "capacity": row["capacity"],
            "parking_lat": row.get("parking_lat"),
            "parking_lng": row.get("parking_lng"),
            "status": row.get("status"),
            "driver_id": row.get("driver_id"),
            "license_no": driver.get("license_no"),
            "driver_name": users.get("name"),
        }