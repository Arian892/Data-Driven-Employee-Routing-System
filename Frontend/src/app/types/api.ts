export type UserRole = "Employee" | "Driver" | "Admin";
export type UserStatus = "Active" | "Inactive";
export type RequestStatus = "Pending" | "Approved" | "Rejected";
export type RequestType = "Regular" | "Ad-hoc";
export type VehicleStatus = "Active" | "Inactive" | "Maintenance";

export interface ApiUser {
  user_id: number;
  name: string;
  email: string;
  phone?: string | null;
  role: UserRole;
  status: UserStatus;
}

export interface AuthLoginResponse {
  user: ApiUser;
  tokens: {
    access_token: string;
    refresh_token?: string | null;
    token_type: string;
  };
}

export interface Pagination {
  current_page: number;
  total_pages: number;
  page_size: number;
  total_items: number;
}

export interface Driver {
  driver_id: number;
  user_id: number;
  name: string;
  email: string;
  phone?: string | null;
  license_no: string;
  status: string;
  user_status?: string | null;
}

export interface Employee {
  user_id: number;
  employee_id: number;
  name: string;
  email: string;
  phone?: string | null;
  home_lat?: number | null;
  home_lng?: number | null;
  role: string;
  status: string;
  is_active: boolean;
}

export interface EmployeeProfileUpdate {
  name?: string;
  phone?: string;
  home_lat?: number;
  home_lng?: number;
}

/** Admin creates an employee account (temp password handed over offline). */
export interface EmployeeCreate {
  name: string;
  email: string;
  phone?: string;
  password: string;
}

export interface ChangePasswordRequest {
  current_password: string;
  new_password: string;
}

export interface ResetPasswordRequest {
  new_password: string;
}

export interface MessageResponse {
  message: string;
}

export interface EmployeesListResponse {
  employees: Employee[];
  pagination: Pagination;
}

export interface DriverCreate {
  name: string;
  email: string;
  phone?: string;
  password: string;
  license_no: string;
  status?: string;
}

export type DriverUpdate = Partial<Pick<DriverCreate, "name" | "phone" | "license_no" | "status">>;

export interface DriverSelfVehicle {
  vehicle_id?: number | null;
  plate_no?: string | null;
  make?: string | null;
  model?: string | null;
}

export interface DriverSelfProfile {
  driver_id: number;
  user_id: number;
  license_no?: string | null;
  status?: string | null;
  name?: string | null;
  email?: string | null;
  phone?: string | null;
  user_status?: string | null;
  vehicle?: DriverSelfVehicle | null;
}

export interface DriverAssignmentPassenger {
  employee_id?: number | null;
  employee_name?: string | null;
  boarded?: boolean | null;
}

/** A drawable path as [lat, lng] pairs, straight from OSRM's road geometry.
 * Null when the backend fell back to haversine, in which case the map should
 * draw the dashed marker-to-marker line instead of pretending it knows roads. */
export type RouteGeometry = [number, number][];

export interface DriverAssignmentStop {
  stop_id?: number | null;
  route_id?: number | null;
  latitude?: number | null;
  longitude?: number | null;
  sequence_order?: number | null;
  arrival_time?: string | null;
  departure_time?: string | null;
  /** "Mazar Road Bus Stop", "Agargaon Metro Station (shared drop point)", ... */
  stop_name?: string | null;
  is_adhoc?: boolean | null;
  is_shared?: boolean | null;
  passengers: DriverAssignmentPassenger[];
}

export interface DriverAssignmentRoute {
  route_id?: number | null;
  zone_id?: number | null;
  zone_name?: string | null;
  route_type?: string | null;
  service_date?: string | null;
  shift_time?: string | null;
  total_distance_km?: number | null;
  total_travel_time_min?: number | null;
  created_at?: string | null;
  route_geometry?: RouteGeometry | null;
  route_code?: string | null;
  stops: DriverAssignmentStop[];
  assignment?: {
    assignment_id?: number | null;
    route_id?: number | null;
    vehicle_id?: number | null;
    driver_id?: number | null;
    departure_time?: string | null;
    arrival_time?: string | null;
    status?: string | null;
  } | null;
}

export interface DriverAssignmentResponse {
  routes: DriverAssignmentRoute[];
}

export interface DriversListResponse {
  drivers: Driver[];
  pagination: Pagination;
}

export interface Vehicle {
  vehicle_id: number;
  plate_no: string;
  capacity: number;
  parking_lat?: number | null;
  parking_lng?: number | null;
  status: VehicleStatus;
  driver_id?: number | null;
  driver_name?: string | null;
  license_no?: string | null;
}

export interface VehicleCreate {
  plate_no: string;
  capacity: number;
  parking_lat?: number | null;
  parking_lng?: number | null;
  status?: VehicleStatus;
  driver_id?: number | null;
}

export type VehicleUpdate = Partial<VehicleCreate>;

export interface VehiclesListResponse {
  vehicles: Vehicle[];
  pagination: Pagination;
}

export interface PickupRequest {
  pickup_id: number;
  employee_id: number;
  employee_name?: string | null;
  zone_id?: number | null;
  zone_name?: string | null;
  pickup_lat?: number | null;
  pickup_lng?: number | null;
  shift_start_time?: string | null;
  service_date: string;
  request_type?: RequestType | null;
  status: RequestStatus;
  pickup_time?: string | null;
  created_at?: string | null;
}

export interface DropoffRequest {
  dropoff_id: number;
  employee_id: number;
  employee_name?: string | null;
  zone_id?: number | null;
  zone_name?: string | null;
  drop_lat?: number | null;
  drop_lng?: number | null;
  shift_end_time?: string | null;
  service_date: string;
  status: RequestStatus;
  drop_time?: string | null;
  created_at?: string | null;
}

export interface DropoffRequestsListResponse {
  dropoff_requests: DropoffRequest[];
  pagination: Pagination;
}

export interface PickupRequestsListResponse {
  pickup_requests: PickupRequest[];
  pagination: Pagination;
}

// ── Weekly requests (Fri/Sat window → next week Sun–Sat) ───────

export interface WeeklyDayRequestPayload {
  shift_start_time: string; // "HH:MM"
  shift_end_time: string;   // "HH:MM"
  pickup_lat: number;
  pickup_lng: number;
  drop_lat: number;
  drop_lng: number;
}

export type WeeklyDayKey =
  | "sun" | "mon" | "tue" | "wed" | "thu" | "fri" | "sat";

export interface WeeklyRequestPayload {
  sun?: WeeklyDayRequestPayload | null;
  mon?: WeeklyDayRequestPayload | null;
  tue?: WeeklyDayRequestPayload | null;
  wed?: WeeklyDayRequestPayload | null;
  thu?: WeeklyDayRequestPayload | null;
  fri?: WeeklyDayRequestPayload | null;
  sat?: WeeklyDayRequestPayload | null;
}

export interface WeeklyWindowView {
  open: boolean;
  opens: string;
  closes: string;
  next_open: string;
  closed_reason?: string | null;
}

export interface WeeklyDayView {
  date: string;
  pickup?: PickupRequest | null;
  dropoff?: DropoffRequest | null;
}

export interface WeeklyRequestView {
  open: boolean;
  window: WeeklyWindowView;
  service_start: string;
  service_end: string;
  week: Record<WeeklyDayKey, WeeklyDayView | null>;
}

// ── Ad-hoc requests (same-day, before 7 PM) ───────────────────

export interface AdhocRequestPayload {
  shift_start_time: string; // "HH:MM"
  shift_end_time: string;   // "HH:MM"
  pickup_lat: number;
  pickup_lng: number;
  drop_lat: number;
  drop_lng: number;
}

export interface AdhocRequestView {
  open: boolean;
  service_date: string;
  cutoff: string;
  reason?: string | null;
  existing: {
    pickup?: PickupRequest | null;
    dropoff?: DropoffRequest | null;
  };
}

export interface ScheduleStop {
  sequence_order: number;
  latitude: number;
  longitude: number;
  arrival_time?: string;
  departure_time?: string;
  stop_name?: string | null;
  is_adhoc?: boolean | null;
  is_shared?: boolean | null;
}

export interface RouteStopInfo extends ScheduleStop {
  stop_id: number;
  is_mine: boolean;
}

/** One half of the night: the ride in, or the ride home. */
export interface ScheduleLeg {
  route_id: number;
  route_type: "pickup" | "dropoff";
  shift_time?: string;
  route_geometry?: RouteGeometry | null;
  stop: ScheduleStop;
  /** Every stop on this route, in visiting order — every colleague sharing
   * the vehicle, not just this employee's own `stop` above. */
  stops?: RouteStopInfo[];
  driver?: { name?: string; phone?: string } | null;
  vehicle?: { plate_no?: string; capacity?: number } | null;
}

export interface ScheduleResponse {
  routing_done: boolean;
  /** Both legs share one service_date, so both are returned. */
  pickup?: ScheduleLeg | null;
  dropoff?: ScheduleLeg | null;
  /** Mirrors `pickup` (or `dropoff`, on a dropoff-only day). */
  route_type?: "pickup" | "dropoff";
  shift_time?: string;
  route_geometry?: RouteGeometry | null;
  stop?: ScheduleStop;
  driver?: {
    name?: string;
    phone?: string;
  };
  vehicle?: {
    plate_no?: string;
    capacity?: number;
  };
}

export interface PickupRoutingZoneCount {
  zone_id?: number | null;
  zone_name?: string | null;
  total_requests: number;
  pending: number;
  approved: number;
  rejected: number;
}

export interface PickupRoutingInputResponse {
  service_date: string;
  shift_start_time?: string | null;
  total_requests: number;
  pending: number;
  approved: number;
  rejected: number;
  zones: PickupRoutingZoneCount[];
}

export interface PickupRoutingRunPayload {
  service_date: string;
  shift_start_time?: string | null;
  office_lat?: number;
  office_lng?: number;
  office_buffer_minutes?: number;
  stop_dwell_minutes?: number;
  average_speed_kmph?: number;
}

export interface DropoffRoutingRunPayload {
  service_date: string;
  shift_end_time?: string | null;
  office_lat?: number;
  office_lng?: number;
  office_buffer_minutes?: number;
  stop_dwell_minutes?: number;
  average_speed_kmph?: number;
}

/** Why one request could not be placed. The reason decides who acts:
 *  `no_coordinates` is a roster data problem, `no_vehicle_available` means the
 *  fleet is short for that shift, and the other two are schedule pressure. */
export interface UnassignedEntry {
  employee_id?: number | null;
  employee_name?: string | null;
  employee_email?: string | null;
  request_type: "pickup" | "dropoff";
  shift_time?: string | null;
  reason:
    | "no_coordinates"
    | "no_vehicle_available"
    | "dropped_for_120min_cap"
    | "vehicle_not_free_in_time"
    | string;
  vehicle_id?: number | null;
  plate_no?: string | null;
}

export interface RoutingRunResponse {
  routes_created: number;
  employees_assigned: number;
  /** Legacy bare-id list, kept so nothing that reads it breaks. */
  unassigned_pickup_ids: number[];
  unassigned?: UnassignedEntry[];
  /** "osrm" | "haversine" — a haversine run means approximate distances and no
   *  road geometry, which is worth telling the admin rather than hiding. */
  engine?: string | null;
  /** Data-quality problems that are NOT unassigned requests, e.g. a vehicle
   *  with no parking coordinates. */
  warnings?: string[];
  message?: string | null;
}

export interface RunDayRoutingResponse {
  service_date: string;
  engine: string;
  counts: Record<string, number>;
  db_calls?: number;
  pickup: RoutingRunResponse;
  dropoff: RoutingRunResponse;
}

export interface RouteAssignmentResponse {
  assignment_id: number;
  route_id: number;
  vehicle_id: number;
  driver_id?: number | null;
  departure_time?: string | null;
  arrival_time?: string | null;
  status: string;
}

export interface RouteStopPassenger {
  employee_id?: number | null;
  employee_name?: string | null;
  boarded?: boolean | null;
}

export interface RouteStopResponse {
  stop_id: number;
  route_id: number;
  latitude: number;
  longitude: number;
  sequence_order: number;
  arrival_time?: string | null;
  departure_time?: string | null;
  /** "Mazar Road Bus Stop", "Agargaon Metro Station (shared drop point)", ... */
  stop_name?: string | null;
  is_adhoc?: boolean | null;
  is_shared?: boolean | null;
  passengers: RouteStopPassenger[];
}

export interface RouteDetailResponse {
  route_id: number;
  zone_id?: number | null;
  zone_name?: string | null;
  route_type: string;
  service_date: string;
  shift_time?: string | null;
  total_distance_km?: number | null;
  total_travel_time_min?: number | null;
  created_at?: string | null;
  route_geometry?: RouteGeometry | null;
  route_code?: string | null;
  stops: RouteStopResponse[];
  assignment?: RouteAssignmentResponse | null;
}

export interface ScheduleSummaryResponse {
  routes: RouteDetailResponse[];
}

export interface RouteAssignPayload {
  vehicle_id: number;
  driver_id: number;
  departure_time?: string | null;
  arrival_time?: string | null;
  status?: string;
}
