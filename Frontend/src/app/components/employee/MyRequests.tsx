import React, { useEffect, useRef, useState } from 'react';
import { Sidebar } from '../shared/Sidebar';
import { AddressText } from '../shared/AddressText';
import { Calendar, CalendarDays, ClipboardList, MapPin, Clock, AlertCircle, Route, Loader2, ChevronDown, ChevronRight, ArrowUpRight, ArrowDownLeft, Building2, User as UserIcon, Car } from 'lucide-react';
import { InteractiveMap } from '../shared/InteractiveMap';
import { buildLegMarkers, coordinateLabel, isSyntheticStopName, MapLegend } from '../shared/ScheduleLeg';
import { OFFICE_LOCATION } from '../../data/mockData';
import { dropoffRequestApi, employeeApi, pickupRequestApi } from '../../services/transportApi';
import type { DropoffRequest, PickupRequest, RequestStatus, ScheduleLeg, ScheduleResponse } from '../../types/api';

type RequestTab = 'all' | 'routed' | 'pending' | 'rejected';
type CombinedRequest = {
  id: string;
  rawId: number;
  type: 'pickup' | 'dropoff';
  requestType: string;
  status: RequestStatus;
  serviceDate: string;
  shiftTime: string;
  location: string;
  latitude: number;
  longitude: number;
  createdAt?: string | null;
};

/** One requested day, combining its pickup + dropoff rows. A date that has both
 * a weekly request and an ad-hoc request becomes two day entries (one per type). */
type DayEntry = {
  serviceDate: string;
  requestType: 'Regular' | 'Ad-hoc';
  pickup?: CombinedRequest;
  dropoff?: CombinedRequest;
};

/** All requested days that fall in the same Sunday→Saturday service week. */
type WeekGroup = {
  weekStart: string; // ISO Sunday of the week
  days: DayEntry[];
};

// ── Date helpers ─────────────────────────────────────────────────────────────

const toISODate = (d: Date): string => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const isoPlusDays = (iso: string, n: number): string => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return toISODate(d);
};

const weekStartOf = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() - d.getDay()); // getDay(): 0 = Sunday
  return toISODate(d);
};

const weekLabel = (weekStart: string): string => {
  const fmt = (iso: string) =>
    new Date(`${iso}T00:00:00`).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
  return `${fmt(weekStart)} – ${fmt(isoPlusDays(weekStart, 6))}`;
};

const dayLabel = (iso: string): string => {
  const d = new Date(`${iso}T00:00:00`);
  return `${d.toLocaleDateString('en-US', { weekday: 'short' })}, ${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}`;
};

// ── Normalizers ──────────────────────────────────────────────────────────────

const normalizePickup = (request: PickupRequest): CombinedRequest => ({
  id: `pickup-${request.pickup_id}`,
  rawId: request.pickup_id,
  type: 'pickup',
  requestType: request.request_type ?? 'Regular',
  status: request.status,
  serviceDate: request.service_date,
  shiftTime: request.shift_start_time ?? '-',
  location: coordinateLabel(request.pickup_lat, request.pickup_lng),
  latitude: request.pickup_lat ?? OFFICE_LOCATION.latitude,
  longitude: request.pickup_lng ?? OFFICE_LOCATION.longitude,
  createdAt: request.created_at,
});

const normalizeDropoff = (request: DropoffRequest): CombinedRequest => ({
  id: `dropoff-${request.dropoff_id}`,
  rawId: request.dropoff_id,
  type: 'dropoff',
  requestType: 'Dropoff',
  status: request.status,
  serviceDate: request.service_date,
  shiftTime: request.shift_end_time ?? '-',
  location: coordinateLabel(request.drop_lat, request.drop_lng),
  latitude: request.drop_lat ?? OFFICE_LOCATION.latitude,
  longitude: request.drop_lng ?? OFFICE_LOCATION.longitude,
  createdAt: request.created_at,
});

/** Group pickup + dropoff rows by service week — one day entry per
 * (serviceDate, requestType), so an ad-hoc request shows as its own entry
 * alongside the day's weekly request. */
const buildGroups = (requests: CombinedRequest[]): WeekGroup[] => {
  const pickups = requests.filter(r => r.type === 'pickup');
  const dropoffs = requests.filter(r => r.type === 'dropoff');

  const byWeek = new Map<string, Map<string, DayEntry>>();
  const entries = new Map<string, DayEntry>();

  const getOrCreate = (serviceDate: string, requestType: 'Regular' | 'Ad-hoc'): DayEntry => {
    const key = `${serviceDate}|${requestType}`;
    let entry = entries.get(key);
    if (!entry) {
      entry = { serviceDate, requestType };
      entries.set(key, entry);
    }
    return entry;
  };

  const createdMs = (iso?: string | null) => (iso ? new Date(iso).getTime() : 0);

  for (const req of pickups) {
    const entry = getOrCreate(req.serviceDate, req.requestType === 'Ad-hoc' ? 'Ad-hoc' : 'Regular');
    // Duplicate rows on the same (date, type) can exist when a week was edited
    // after routing. Always show the NEWEST submission, not whichever row the
    // API happened to return last.
    if (!entry.pickup || createdMs(req.createdAt) > createdMs(entry.pickup.createdAt)) {
      entry.pickup = req;
    }
  }

  // Dropoffs carry no request_type — attach each to the pickup on the same date
  // whose created_at is nearest (weekly rows were written together in the request
  // window; ad-hoc rows together on the day), and inherit its type.
  for (const drop of dropoffs) {
    const sameDate = pickups.filter(p => p.serviceDate === drop.serviceDate);
    const created = (iso: string | null | undefined) => new Date(iso ?? 0).getTime();
    const nearest = sameDate.reduce<CombinedRequest | null>((best, p) => {
      if (best == null) return p;
      return Math.abs(created(p.createdAt) - created(drop.createdAt)) <=
             Math.abs(created(best.createdAt) - created(drop.createdAt)) ? p : best;
    }, null);
    const entry = nearest
      ? getOrCreate(drop.serviceDate, nearest.requestType === 'Ad-hoc' ? 'Ad-hoc' : 'Regular')
      : getOrCreate(drop.serviceDate, 'Regular');
    entry.dropoff = drop;
  }

  for (const [key, entry] of entries) {
    const ws = weekStartOf(entry.serviceDate);
    if (!byWeek.has(ws)) byWeek.set(ws, new Map());
    byWeek.get(ws)!.set(key, entry);
  }

  const groups: WeekGroup[] = [];
  for (const [weekStart, byDate] of byWeek) {
    groups.push({
      weekStart,
      days: [...byDate.values()].sort((a, b) =>
        a.serviceDate.localeCompare(b.serviceDate) ||
        a.requestType.localeCompare(b.requestType),
      ),
    });
  }
  groups.sort((a, b) => b.weekStart.localeCompare(a.weekStart));
  return groups;
};

const dayStatus = (day: DayEntry): RequestStatus =>
  day.pickup?.status ?? day.dropoff?.status ?? 'Pending';

const groupStatus = (g: WeekGroup): RequestStatus => {
  const statuses = new Set(g.days.map(dayStatus));
  if (statuses.size === 1) return [...statuses][0];
  if (statuses.has('Approved')) return 'Approved';
  if (statuses.has('Pending')) return 'Pending';
  return 'Rejected';
};

const groupTitle = (g: WeekGroup): string => {
  const hasWeekly = g.days.some(d => d.pickup?.requestType === 'Regular');
  const hasAdhoc = g.days.some(d => d.pickup?.requestType === 'Ad-hoc');
  if (hasWeekly && hasAdhoc) return 'Transport Requests';
  return hasWeekly ? 'Weekly Request' : 'Ad-hoc Requests';
};

// ── Expanded-day sub-sections ────────────────────────────────────────────────

type JourneyPoint = {
  label: string;
  title: React.ReactNode;
  sub?: React.ReactNode;
  icon: React.ReactNode;
  accent: string; // bg + text classes for the icon chip
};

/** "Pickup → Your Stop → Office" (or the reverse for dropoff) — the assigned
 * stop can differ from what was originally requested (a shared main-road
 * drop instead of door-to-door), so this makes that visible at a glance
 * instead of only appearing lower down in the route detail text. */
const JourneyFlow: React.FC<{ points: JourneyPoint[] }> = ({ points }) => (
  <div className="flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-1.5">
    {points.map((p, i) => (
      <React.Fragment key={i}>
        <div className="flex items-start gap-2.5 sm:flex-1 sm:min-w-0">
          <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${p.accent}`}>
            {p.icon}
          </div>
          <div className="min-w-0">
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">{p.label}</p>
            <p className="text-sm font-medium text-foreground truncate">{p.title}</p>
            {p.sub && <p className="text-xs text-muted-foreground truncate mt-0.5">{p.sub}</p>}
          </div>
        </div>
        {i < points.length - 1 && (
          <ChevronRight className="hidden sm:block w-4 h-4 text-muted-foreground flex-shrink-0" />
        )}
      </React.Fragment>
    ))}
  </div>
);

/** Route-detail column that sits beside the (untouched) map. Driver/vehicle
 * live in their own section below, not mixed in here. */
const RouteDetailPanel: React.FC<{ leg: ScheduleLeg }> = ({ leg }) => {
  const isPickup = leg.route_type === 'pickup';
  const namedStop = leg.stop.stop_name && !isSyntheticStopName(leg.stop.stop_name, leg.stop.is_adhoc);
  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center gap-2 mb-3">
        {isPickup
          ? <ArrowUpRight className="w-4 h-4 text-sky-600 dark:text-sky-400 flex-shrink-0" />
          : <ArrowDownLeft className="w-4 h-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />}
        <p className={`text-xs font-semibold uppercase tracking-wider ${isPickup ? 'text-sky-600 dark:text-sky-400' : 'text-emerald-600 dark:text-emerald-400'}`}>
          {isPickup ? 'Pickup Route' : 'Dropoff Route'}
        </p>
      </div>

      <div className="space-y-3 flex-1 flex flex-col justify-center">
        <p className="text-sm text-foreground">
          {isPickup ? 'Ride to office' : 'Ride home'} · stop {leg.stop.sequence_order}
          {leg.shift_time ? ` · shift ${leg.shift_time.slice(0, 5)}` : ''}
        </p>

        <div className="flex items-start gap-2">
          <MapPin className="w-4 h-4 text-muted-foreground mt-0.5 flex-shrink-0" />
          <div className="text-sm min-w-0">
            {namedStop ? (
              <p className="text-foreground">{leg.stop.stop_name}</p>
            ) : (
              <AddressText lat={leg.stop.latitude} lng={leg.stop.longitude} className="text-foreground" />
            )}
            {leg.stop.is_shared && (
              <p className="text-xs text-violet-600 dark:text-violet-400 mt-0.5">Shared drop point — walk from here to your home.</p>
            )}
            <p className="text-xs text-muted-foreground font-mono mt-0.5">{coordinateLabel(leg.stop.latitude, leg.stop.longitude)}</p>
          </div>
        </div>

        {leg.stop.arrival_time && (
          <div className="flex items-center gap-2">
            <Clock className="w-4 h-4 text-amber-600 dark:text-amber-400 flex-shrink-0" />
            <p className="text-sm text-foreground">
              {isPickup ? 'Pickup at' : 'Dropoff at'}: {leg.stop.arrival_time}
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

/** Compact, dedicated driver/vehicle strip — pulled out of the route
 * description so it reads as its own fact rather than buried mid-paragraph. */
const DriverVehicleRow: React.FC<{ leg: ScheduleLeg }> = ({ leg }) => {
  if (!leg.driver && !leg.vehicle) return null;
  return (
    <div className="flex flex-wrap gap-x-8 gap-y-3 pt-4 mt-4 border-t border-border">
      {leg.driver && (
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-muted border border-border flex items-center justify-center flex-shrink-0">
            <UserIcon className="w-4 h-4 text-foreground" />
          </div>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Driver</p>
            <p className="text-sm text-foreground">
              {leg.driver.name}{leg.driver.phone ? ` · ${leg.driver.phone}` : ''}
            </p>
          </div>
        </div>
      )}
      {leg.vehicle && (
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-lg bg-muted border border-border flex items-center justify-center flex-shrink-0">
            <Car className="w-4 h-4 text-foreground" />
          </div>
          <div>
            <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">Vehicle</p>
            <p className="text-sm text-foreground">{leg.vehicle.plate_no ?? '—'}</p>
          </div>
        </div>
      )}
    </div>
  );
};

/** Builds the 3-node "Pickup → Your Stop → Office" (or reverse) flow for one
 * leg, using the originally-requested location (may differ from the assigned
 * stop, e.g. a shared main-road drop instead of door-to-door). */
const journeyPointsFor = (leg: ScheduleLeg, origin?: CombinedRequest, destinationLabel?: string): JourneyPoint[] => {
  const isPickup = leg.route_type === 'pickup';
  const namedStop = leg.stop.stop_name && !isSyntheticStopName(leg.stop.stop_name, leg.stop.is_adhoc);
  const yourStop: JourneyPoint = {
    label: 'Your Stop',
    title: namedStop ? leg.stop.stop_name : <AddressText lat={leg.stop.latitude} lng={leg.stop.longitude} className="text-foreground" />,
    sub: `Stop ${leg.stop.sequence_order}${leg.stop.arrival_time ? ` · ${leg.stop.arrival_time}` : ''}`,
    icon: <MapPin className="w-4 h-4 text-amber-600 dark:text-amber-400" />,
    accent: 'bg-amber-50 dark:bg-amber-400/20',
  };
  const officePoint: JourneyPoint = {
    label: 'Office',
    title: 'Office',
    icon: <Building2 className="w-4 h-4 text-foreground" />,
    accent: 'bg-muted border border-border',
  };
  const requestedPoint: JourneyPoint = {
    label: isPickup ? 'Pickup' : 'Dropoff',
    title: origin
      ? <AddressText lat={origin.latitude} lng={origin.longitude} className="text-foreground" />
      : (destinationLabel ?? '—'),
    icon: isPickup ? <ArrowUpRight className="w-4 h-4 text-sky-600 dark:text-sky-400" /> : <ArrowDownLeft className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />,
    accent: isPickup ? 'bg-sky-50 dark:bg-sky-400/20' : 'bg-emerald-50 dark:bg-emerald-400/20',
  };
  return isPickup ? [requestedPoint, yourStop, officePoint] : [officePoint, yourStop, requestedPoint];
};

export const MyRequests: React.FC = () => {
  const [activeTab, setActiveTab] = useState<RequestTab>('all');
  const [expandedKey, setExpandedKey] = useState<string | null>(null);
  const [groups, setGroups] = useState<WeekGroup[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [cancelingDate, setCancelingDate] = useState<string | null>(null);
  const [scheduleCache, setScheduleCache] = useState<Record<string, ScheduleResponse>>({});
  const [scheduleLoading, setScheduleLoading] = useState<Record<string, boolean>>({});
  // Guards against firing the same date's fetch twice. Lives in a ref so the
  // fetch effect below doesn't re-run when scheduleLoading/scheduleCache
  // change (which used to cancel the in-flight request and leave the spinner
  // stuck on "Loading your route…" forever).
  const inFlightRef = useRef<Record<string, boolean>>({});

  // When an approved day is expanded, fetch the employee's real schedule for
  // that service date (stop + driver + vehicle) and cache it per date.
  useEffect(() => {
    if (!expandedKey) return;
    const [, serviceDate] = expandedKey.split('|');
    const day = groups.flatMap(g => g.days).find(d => d.serviceDate === serviceDate);
    if (!day || dayStatus(day) !== 'Approved') return;
    if (scheduleCache[serviceDate] || inFlightRef.current[serviceDate]) return;

    inFlightRef.current[serviceDate] = true;
    setScheduleLoading(prev => ({ ...prev, [serviceDate]: true }));
    employeeApi
      .getSchedule(serviceDate)
      .then(schedule => {
        setScheduleCache(prev => ({ ...prev, [serviceDate]: schedule }));
      })
      .catch(() => {
        // Leave the cache empty so re-expanding retries.
      })
      .finally(() => {
        inFlightRef.current[serviceDate] = false;
        setScheduleLoading(prev => ({ ...prev, [serviceDate]: false }));
      });
    // Only re-run when a *different* day is expanded or the data reloads —
    // not when the fetch updates scheduleLoading/scheduleCache.
  }, [expandedKey, groups]);

  const loadRequests = async () => {
    setIsLoading(true);
    setError(null);

    try {
      const [pickups, dropoffs] = await Promise.all([
        pickupRequestApi.mine({ limit: 500 }),
        dropoffRequestApi.mine({ limit: 500 }),
      ]);

      setGroups(
        buildGroups([
          ...pickups.pickup_requests.map(normalizePickup),
          ...dropoffs.dropoff_requests.map(normalizeDropoff),
        ]),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load requests');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    void loadRequests();
  }, []);

  const filteredGroups = groups
    .map(g => ({
      ...g,
      days: g.days.filter(d => {
        const s = dayStatus(d);
        if (activeTab === 'all') return true;
        if (activeTab === 'routed') return s === 'Approved';
        if (activeTab === 'pending') return s === 'Pending';
        return s === 'Rejected';
      }),
    }))
    .filter(g => g.days.length > 0);

  const allDays = groups.flatMap(g => g.days);
  const counts = {
    all: allDays.length,
    routed: allDays.filter(d => dayStatus(d) === 'Approved').length,
    pending: allDays.filter(d => dayStatus(d) === 'Pending').length,
    rejected: allDays.filter(d => dayStatus(d) === 'Rejected').length,
  };

  const handleCancel = async (day: DayEntry) => {
    setCancelingDate(day.serviceDate);
    setError(null);
    try {
      const ops: Promise<unknown>[] = [];
      if (day.pickup) ops.push(pickupRequestApi.remove(day.pickup.rawId));
      if (day.dropoff) ops.push(dropoffRequestApi.remove(day.dropoff.rawId));
      await Promise.all(ops);
      await loadRequests();
      if (expandedKey?.endsWith(day.serviceDate)) setExpandedKey(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel request');
    } finally {
      setCancelingDate(null);
    }
  };

  const StatusBadge = ({ status }: { status: string }) => {
    const map: Record<string, { label: string; dot: string; text: string }> = {
      Approved: { label: 'Approved', dot: 'bg-emerald-500', text: 'text-emerald-600 dark:text-emerald-400' },
      Pending: { label: 'Pending', dot: 'bg-amber-500', text: 'text-amber-600 dark:text-amber-400' },
      Rejected: { label: 'Rejected', dot: 'bg-red-500', text: 'text-red-600 dark:text-red-400' },
    };
    const m = map[status] || map.Pending;
    return (
      <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full bg-muted border border-border font-medium ${m.text}`}>
        <span className={`w-1.5 h-1.5 rounded-full ${m.dot}`} />
        {m.label}
      </span>
    );
  };

  return (
    <Sidebar role="employee">
      <div className="p-6 max-w-[1600px] mx-auto">
        <div className="flex items-center gap-4 pb-6 mb-6 border-b border-border">
          <div className="w-12 h-12 rounded-2xl bg-muted border border-border flex items-center justify-center flex-shrink-0 shadow-sm">
            <ClipboardList className="w-6 h-6 text-foreground" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>My Requests</h1>
            <p className="text-muted-foreground text-sm mt-0.5">
              Your requests grouped by service week — edit them any time in the request window.
            </p>
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/8 px-5 py-4 mb-6">
            <AlertCircle className="w-4 h-4 text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-red-600 dark:text-red-400/90">{error}</p>
          </div>
        )}

        {/* Filter tabs — one segmented control, not four floating pills, so
            the four states read as views of one list rather than separate
            buttons that happen to sit near each other. */}
        <div className="inline-flex items-center gap-1 p-1 mb-6 rounded-xl overflow-x-auto max-w-full bg-muted">
          {([
            ['all', 'All Requests', 'text-muted-foreground'],
            ['routed', 'Routed', 'text-emerald-600 dark:text-emerald-400'],
            ['pending', 'Pending', 'text-amber-600 dark:text-amber-400'],
            ['rejected', 'Rejected', 'text-red-600 dark:text-red-400'],
          ] as [typeof activeTab, string, string][]).map(([key, label, color]) => (
            <button
              key={key}
              onClick={() => setActiveTab(key)}
              className={`flex-shrink-0 flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all duration-150 ${
                activeTab === key
                  ? 'bg-card text-foreground shadow-sm'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {label}
              <span className={`text-xs font-bold ${activeTab === key ? color : 'text-muted-foreground'}`}>
                {counts[key]}
              </span>
            </button>
          ))}
        </div>

        {/* Week cards */}
        <div className="space-y-4">
          {isLoading && (
            <div className="text-center py-16 text-muted-foreground">
              <Clock className="w-10 h-10 mx-auto mb-3 opacity-30 animate-pulse" />
              <p>Loading your requests...</p>
            </div>
          )}

          {!isLoading && filteredGroups.length === 0 && (
            <div className="text-center py-16 text-muted-foreground">
              <Calendar className="w-10 h-10 mx-auto mb-3 opacity-30" />
              <p>No requests in this category.</p>
            </div>
          )}

          {filteredGroups.map(group => (
            <div key={group.weekStart} className="rounded-xl bg-card border border-border overflow-hidden shadow-sm">
              {/* Card header */}
              <div className="flex items-center justify-between px-5 py-4">
                <div className="flex items-center gap-4">
                  <div className="w-9 h-9 rounded-lg bg-muted border border-border flex items-center justify-center">
                    <CalendarDays className="w-4 h-4 text-foreground" />
                  </div>
                  <div>
                    <p className="text-base font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>{groupTitle(group)}</p>
                    <p className="text-xs text-muted-foreground mt-0.5">
                      {weekLabel(group.weekStart)} · {group.days.length} day{group.days.length > 1 ? 's' : ''}
                    </p>
                  </div>
                </div>
                <StatusBadge status={groupStatus(group)} />
              </div>

              {/* Day rows */}
              <div className="border-t border-border">
                {group.days.map(day => {
                  const key = `${group.weekStart}|${day.serviceDate}|${day.requestType}`;
                  const isExpanded = expandedKey === key;
                  const status = dayStatus(day);
                  const schedule = scheduleCache[day.serviceDate];
                  // Both halves of the night, in travel order. Usually two; one
                  // if only that half got routed.
                  const legs: ScheduleLeg[] = [schedule?.pickup, schedule?.dropoff]
                    .filter((l): l is ScheduleLeg => Boolean(l));
                  const fallbackLat = day.pickup?.latitude ?? OFFICE_LOCATION.latitude;
                  const fallbackLng = day.pickup?.longitude ?? OFFICE_LOCATION.longitude;

                  return (
                    <div key={key} className="border-t border-border first:border-t-0">
                      <div
                        className="flex items-center justify-between px-5 py-3 cursor-pointer hover:bg-muted transition"
                        onClick={() => setExpandedKey(isExpanded ? null : key)}
                      >
                        <div>
                          <p className="text-sm font-medium text-foreground">{dayLabel(day.serviceDate)}</p>
                          <div className="flex items-center gap-4 mt-1 text-xs text-muted-foreground">
                            {day.pickup && (
                              <span className="flex items-center gap-1.5">
                                <ArrowUpRight className="w-3.5 h-3.5 text-sky-600 dark:text-sky-400" />
                                pickup {day.pickup.shiftTime}
                              </span>
                            )}
                            {day.dropoff && (
                              <span className="flex items-center gap-1.5">
                                <ArrowDownLeft className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                                dropoff {day.dropoff.shiftTime}
                              </span>
                            )}
                          </div>
                        </div>
                        <div className="flex items-center gap-3">
                          {day.requestType === 'Ad-hoc' && (
                            <span className="text-xs px-2.5 py-1 rounded-full border font-medium bg-violet-50 dark:bg-violet-400/20 text-violet-700 dark:text-violet-300 border-violet-200 dark:border-violet-400/30">
                              Ad-hoc
                            </span>
                          )}
                          <StatusBadge status={status} />
                          {status === 'Pending' && (
                            <button
                              onClick={(event) => {
                                event.stopPropagation();
                                void handleCancel(day);
                              }}
                              disabled={cancelingDate === day.serviceDate}
                              className="px-3 py-1.5 rounded-lg border border-red-200 dark:border-red-400/30 bg-red-50 dark:bg-red-500/10 text-xs font-medium text-red-600 dark:text-red-300 hover:bg-red-100 dark:hover:bg-red-500/20 transition disabled:opacity-60"
                            >
                              {cancelingDate === day.serviceDate ? 'Canceling...' : 'Cancel'}
                            </button>
                          )}
                          <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${isExpanded ? 'rotate-180' : ''}`} />
                        </div>
                      </div>

                      {/* Expanded day details */}
                      {isExpanded && (
                        <div className="px-5 pb-5">
                          <div className="pt-4 space-y-5">
                            {/* Request summary — a thin accent bar + text, not two
                                heavy boxes, since this is a quick recap rather than
                                the main content of the panel. */}
                            <div className="flex flex-col sm:flex-row gap-4 sm:gap-8 pb-4 border-b border-border">
                              {day.pickup && (
                                <div className="flex items-start gap-2.5 pl-3 border-l-2 border-sky-600/60 dark:border-sky-400/60 flex-1 min-w-0">
                                  <div className="min-w-0">
                                    <p className="text-[10px] font-semibold uppercase tracking-wider text-sky-600 dark:text-sky-400">Pickup Requested</p>
                                    <AddressText lat={day.pickup.latitude} lng={day.pickup.longitude} className="text-sm text-foreground" />
                                    <p className="text-xs text-muted-foreground mt-0.5">Shift start {day.pickup.shiftTime}</p>
                                  </div>
                                </div>
                              )}
                              {day.dropoff && (
                                <div className="flex items-start gap-2.5 pl-3 border-l-2 border-emerald-600/60 dark:border-emerald-400/60 flex-1 min-w-0">
                                  <div className="min-w-0">
                                    <p className="text-[10px] font-semibold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">Dropoff Requested</p>
                                    <AddressText lat={day.dropoff.latitude} lng={day.dropoff.longitude} className="text-sm text-foreground" />
                                    <p className="text-xs text-muted-foreground mt-0.5">Shift end {day.dropoff.shiftTime}</p>
                                  </div>
                                </div>
                              )}
                            </div>

                            {status === 'Approved' && (
                              scheduleLoading[day.serviceDate] ? (
                                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                                  <Loader2 className="w-4 h-4 animate-spin text-emerald-600 dark:text-emerald-400" />
                                  Loading your route...
                                </div>
                              ) : schedule?.routing_done && legs.length > 0 ? (
                                <div className="space-y-6">
                                  {/* One section per leg — pickup and dropoff are separate
                                      vehicles/drivers/routes, so each gets its own map. */}
                                  {legs.map(leg => (
                                    <div key={leg.route_id} className="rounded-xl border border-border bg-muted p-4 sm:p-5 space-y-5">
                                      {/* 2. Journey summary */}
                                      <JourneyFlow points={journeyPointsFor(leg, leg.route_type === 'pickup' ? day.pickup : day.dropoff)} />

                                      {/* 3. Route information + map, as one coherent, equal-weight section */}
                                      <div className="grid grid-cols-1 lg:grid-cols-7 gap-5 pt-1 border-t border-border">
                                        <div className="lg:col-span-2 pt-4">
                                          <RouteDetailPanel leg={leg} />
                                        </div>
                                        <div className="lg:col-span-5 pt-4">
                                          <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">Route Map</p>
                                          <InteractiveMap
                                            center={[leg.stop.latitude, leg.stop.longitude]}
                                            zoom={14}
                                            markers={buildLegMarkers(leg)}
                                            fitToMarkers
                                            showRoute
                                            routeGeometry={leg.route_geometry}
                                            height="440px"
                                            lazy
                                          />
                                          <MapLegend routeType={leg.route_type === 'dropoff' ? 'dropoff' : 'pickup'} />
                                        </div>
                                      </div>

                                      {/* 4. Driver & vehicle — its own compact strip */}
                                      <DriverVehicleRow leg={leg} />
                                    </div>
                                  ))}
                                  {legs.length === 1 && (
                                    <p className="text-xs text-muted-foreground">
                                      {legs[0].route_type === 'pickup'
                                        ? 'Your ride home has not been assigned for this date.'
                                        : 'Your ride to the office has not been assigned for this date.'}
                                    </p>
                                  )}
                                </div>
                              ) : (
                                <div className="rounded-xl border border-border bg-muted p-4 space-y-3">
                                  <div className="flex items-center gap-2">
                                    <Route className="w-4 h-4 text-emerald-600 dark:text-emerald-400" />
                                    <p className="text-sm text-foreground">
                                      Route assigned — stop details will appear after routing completes.
                                    </p>
                                  </div>
                                  <div className="flex items-center gap-2">
                                    <Clock className="w-4 h-4 text-amber-600 dark:text-amber-400" />
                                    <p className="text-sm text-foreground">Scheduled time: {day.pickup?.shiftTime ?? day.dropoff?.shiftTime}</p>
                                  </div>
                                  <div>
                                    <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">Location Map</p>
                                    <InteractiveMap
                                      center={[fallbackLat, fallbackLng]}
                                      zoom={14}
                                      markers={[{ position: [fallbackLat, fallbackLng], label: 'Your Location', variant: 'mine' }]}
                                      height="320px"
                                      lazy
                                    />
                                  </div>
                                </div>
                              )
                            )}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </Sidebar>
  );
};
