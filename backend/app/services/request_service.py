from fastapi import HTTPException
from supabase import Client

class RequestService:
    def __init__(self, db: Client):
        self.db = db

    # ── Pickup Requests ─────────────────────────────────────────

    def approve_pickup(self, pickup_id: int):
        req = self._get_pickup_or_404(pickup_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot approve — request is already '{req['status']}'"
            )
        self.db.table("pickup_request").update({"status": "Approved"}).eq("pickup_id", pickup_id).execute()
        return {"message": f"Pickup request {pickup_id} approved"}

    def reject_pickup(self, pickup_id: int):
        req = self._get_pickup_or_404(pickup_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject — request is already '{req['status']}'"
            )
        self.db.table("pickup_request").update({"status": "Rejected"}).eq("pickup_id", pickup_id).execute()
        return {"message": f"Pickup request {pickup_id} rejected"}

    # ── Dropoff Requests ─────────────────────────────────────────

    def get_all_dropoffs(self, status: str = None, service_date: str = None):
        query = (
            self.db.table("dropoff_request")
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
        )
        if status:
            query = query.eq("status", status)
        if service_date:
            query = query.eq("service_date", service_date)

        res = query.order("created_at", desc=True).execute()
        return [self._flatten_dropoff(r) for r in res.data]

    def get_dropoff_by_id(self, dropoff_id: int):
        res = (
            self.db.table("dropoff_request")
            .select(
                "*, "
                "employee(employee_id, users(name)), "
                "zone(zone_name)"
            )
            .eq("dropoff_id", dropoff_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Dropoff request not found")
        return self._flatten_dropoff(res.data)

    def approve_dropoff(self, dropoff_id: int):
        req = self._get_dropoff_or_404(dropoff_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot approve — request is already '{req['status']}'"
            )
        self.db.table("dropoff_request").update({"status": "Approved"}).eq("dropoff_id", dropoff_id).execute()
        return {"message": f"Dropoff request {dropoff_id} approved"}

    def reject_dropoff(self, dropoff_id: int):
        req = self._get_dropoff_or_404(dropoff_id)
        if req["status"] != "Pending":
            raise HTTPException(
                status_code=409,
                detail=f"Cannot reject — request is already '{req['status']}'"
            )
        self.db.table("dropoff_request").update({"status": "Rejected"}).eq("dropoff_id", dropoff_id).execute()
        return {"message": f"Dropoff request {dropoff_id} rejected"}

    # ── Helpers ─────────────────────────────────────────────────

    def _get_pickup_or_404(self, pickup_id: int):
        res = (
            self.db.table("pickup_request")
            .select("pickup_id, status")
            .eq("pickup_id", pickup_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Pickup request not found")
        return res.data

    def _get_dropoff_or_404(self, dropoff_id: int):
        res = (
            self.db.table("dropoff_request")
            .select("dropoff_id, status")
            .eq("dropoff_id", dropoff_id)
            .single()
            .execute()
        )
        if not res.data:
            raise HTTPException(status_code=404, detail="Dropoff request not found")
        return res.data

    def _flatten_dropoff(self, row: dict) -> dict:
        employee = row.pop("employee", None) or {}
        users = employee.pop("users", None) or {}
        zone = row.pop("zone", None) or {}
        return {
            **row,
            "employee_name": users.get("name"),
            "zone_name": zone.get("zone_name"),
        }