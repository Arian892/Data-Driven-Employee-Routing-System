import React, { useEffect, useRef, useState } from 'react';
import { Sidebar } from '../shared/Sidebar';
import { Send, CheckCircle, Info, AlertTriangle, CalendarDays, Lock, Pencil, Clock } from 'lucide-react';
import { MapLocationPicker, MapLocation } from './MapLocationPicker';
import { OFFICE_LOCATION } from '../../data/mockData';
import { employeeApi, weeklyRequestApi } from '../../services/transportApi';
import type { WeeklyDayKey, WeeklyDayView, WeeklyRequestPayload, WeeklyRequestView } from '../../types/api';

// Shift options — overnight, 10 PM → 6 AM, one hour apart.
const SHIFT_OPTIONS = ['22:00', '23:00', '00:00', '01:00', '02:00', '03:00', '04:00', '05:00', '06:00'];

// All seven days of the target service week, in Sunday→Saturday order.
// The same order the backend uses (see WEEK_DAY_KEYS in week_service.py).
const WEEK_DAY_KEYS: WeeklyDayKey[] = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat'];

// An employee can request at most 5 days in a week.
const MAX_DAYS = 5;

const DHAKA_CENTER: [number, number] = [23.7808, 90.4043];

// Cache reverse-geocoded addresses by coordinates so repeat visits show the
// readable address instantly instead of waiting on the geocoder again.
const HOME_ADDR_CACHE_KEY = 'home_address_cache';

const cachedHomeAddress = (lat: number, lng: number): string | null => {
  try {
    const cache = JSON.parse(localStorage.getItem(HOME_ADDR_CACHE_KEY) || '{}');
    return cache[`${lat},${lng}`] ?? null;
  } catch {
    return null;
  }
};

const cacheHomeAddress = (lat: number, lng: number, address: string) => {
  try {
    const cache = JSON.parse(localStorage.getItem(HOME_ADDR_CACHE_KEY) || '{}');
    cache[`${lat},${lng}`] = address;
    localStorage.setItem(HOME_ADDR_CACHE_KEY, JSON.stringify(cache));
  } catch {
    /* ignore */
  }
};

const blankLocation = (): MapLocation => ({
  address: '',
  lat: DHAKA_CENTER[0],
  lng: DHAKA_CENTER[1],
  pinned: false,
});

interface DayBooking {
  shiftStart: string;
  shiftEnd: string;
  pickup: MapLocation;
  dropoff: MapLocation;
}

const defaultBooking = (home: MapLocation | null): DayBooking => {
  const loc = home ?? blankLocation();
  return { shiftStart: '22:00', shiftEnd: '06:00', pickup: loc, dropoff: loc };
};

const OFFICE_MARKER = {
  label: OFFICE_LOCATION.name,
  lat: OFFICE_LOCATION.latitude,
  lng: OFFICE_LOCATION.longitude,
};

/** Build a pinned location from DB coordinates (readable when cached). */
const locFromCoords = (lat?: number | null, lng?: number | null): MapLocation => {
  if (lat == null || lng == null) return blankLocation();
  return {
    address: cachedHomeAddress(lat, lng) ?? `${lat.toFixed(5)}, ${lng.toFixed(5)}`,
    lat,
    lng,
    pinned: true,
  };
};

/** Reverse-geocode coordinates to a readable address (with cache). */
const reverseGeocode = async (lat: number, lng: number): Promise<string> => {
  const cached = cachedHomeAddress(lat, lng);
  if (cached) return cached;
  try {
    const res = await fetch(
      `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lng}`,
    );
    const data = (await res.json()) as { display_name?: string };
    const addr = data.display_name ?? `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
    cacheHomeAddress(lat, lng, addr);
    return addr;
  } catch {
    return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
  }
};

const toISO = (d: Date) => {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

interface DayLabel {
  full: string;
  short: string;
  label: string;
}

const labelFromISO = (iso: string): DayLabel => {
  const d = new Date(`${iso}T00:00:00`);
  return {
    full: d.toLocaleDateString('en-US', { weekday: 'long' }),
    short: d.toLocaleDateString('en-US', { weekday: 'short' }),
    label: d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
  };
};

/** Add `n` days to an ISO date string. */
const isoPlusDays = (iso: string, n: number): string => {
  const d = new Date(`${iso}T00:00:00`);
  d.setDate(d.getDate() + n);
  return toISO(d);
};

/** The ISO date for one day key, from the view (falling back to service_start + offset). */
const isoForDay = (view: WeeklyRequestView, key: WeeklyDayKey): string => {
  const dayView = view.week[key];
  if (dayView?.date) return dayView.date;
  return isoPlusDays(view.service_start, WEEK_DAY_KEYS.indexOf(key));
};

const labelFor = (view: WeeklyRequestView, key: WeeklyDayKey): DayLabel =>
  labelFromISO(isoForDay(view, key));

/** True if `t` ("HH:MM") is one of the selectable overnight shift times. */
const isNightShift = (t?: string | null): boolean =>
  !!t && SHIFT_OPTIONS.includes(t.slice(0, 5));

/** Build the day booking for a day, prefilled from an existing saved request. */
const dayFromView = (day: WeeklyDayView | null, home: MapLocation | null): DayBooking => {
  if (day?.pickup && day?.dropoff) {
    const savedStart = (day.pickup.shift_start_time ?? '').slice(0, 5);
    const savedEnd = (day.dropoff.shift_end_time ?? '').slice(0, 5);
    // Requests saved before the overnight-only change can carry day-shift times
    // (e.g. 07:00/19:00) that are no longer selectable; fall back to the default
    // overnight pair so the stale value never resubmits and gets rejected.
    return {
      shiftStart: isNightShift(savedStart) ? savedStart : '22:00',
      shiftEnd: isNightShift(savedEnd) ? savedEnd : '06:00',
      pickup: locFromCoords(day.pickup.pickup_lat, day.pickup.pickup_lng),
      dropoff: locFromCoords(day.dropoff.drop_lat, day.dropoff.drop_lng),
    };
  }
  return defaultBooking(home);
};

const fmtCountdown = (ms: number): string => {
  if (ms <= 0) return '0m 0s';
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d) return `${d}d ${h}h ${m}m`;
  if (h) return `${h}h ${m}m ${sec}s`;
  return `${m}m ${sec}s`;
};

const allFalse = (): Record<WeeklyDayKey, boolean> =>
  Object.fromEntries(WEEK_DAY_KEYS.map(k => [k, false])) as Record<WeeklyDayKey, boolean>;

export const PickupDropoffRequestForm: React.FC = () => {
  const [weekly, setWeekly] = useState<WeeklyRequestView | null>(null);
  const [loading, setLoading] = useState(true);
  const [editMode, setEditMode] = useState(false);
  const [bookings, setBookings] = useState<Record<WeeklyDayKey, DayBooking>>(() =>
    Object.fromEntries(WEEK_DAY_KEYS.map(k => [k, defaultBooking(null)])) as Record<WeeklyDayKey, DayBooking>,
  );
  const [enabled, setEnabled] = useState<Record<WeeklyDayKey, boolean>>(allFalse);
  const [home, setHome] = useState<MapLocation | null>(null);
  const [now, setNow] = useState<Date>(() => new Date());
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [step, setStep] = useState<'form' | 'success'>('form');
  const submittedDays = useRef<WeeklyDayKey[]>([]);

  // Live countdown tick.
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Load the employee's home address + this week's saved request.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, weeklyView] = await Promise.all([
          employeeApi.getProfile(),
          weeklyRequestApi.current(),
        ]);
        if (cancelled) return;

        let home: MapLocation | null = null;
        if (profile.home_lat != null && profile.home_lng != null) {
          const { home_lat: lat, home_lng: lng } = profile;
          home = locFromCoords(lat, lng);
          // Upgrade the coordinate fallback to a readable street address.
          if (!cachedHomeAddress(lat, lng)) {
            reverseGeocode(lat, lng).then(addr => {
              if (cancelled) return;
              setBookings(prev => patchAllAt(prev, lat, lng, addr));
            });
          }
        }
        setHome(home);

        setWeekly(weeklyView);
        const nextBookings = {} as Record<WeeklyDayKey, DayBooking>;
        const nextEnabled = {} as Record<WeeklyDayKey, boolean>;
        const toResolve: MapLocation[] = [];
        let hasSaved = false;
        for (const key of WEEK_DAY_KEYS) {
          const view = weeklyView.week[key] ?? null;
          const b = dayFromView(view, home);
          nextBookings[key] = b;
          const hasRow = !!view?.pickup;
          if (hasRow) hasSaved = true;
          // Zero selected by default; only days with a saved request start on.
          nextEnabled[key] = hasRow;
          toResolve.push(b.pickup, b.dropoff);
        }
        setBookings(nextBookings);
        setEditMode(hasSaved);
        setEnabled(nextEnabled);

        // Upgrade any prefilled saved locations that only have coordinates.
        for (const loc of toResolve) {
          if (loc.pinned && !cachedHomeAddress(loc.lat, loc.lng)) {
            reverseGeocode(loc.lat, loc.lng).then(addr => {
              if (cancelled) return;
              setBookings(prev => patchAllAt(prev, loc.lat, loc.lng, addr));
            });
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load weekly request');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  // Replace the address of every pinned location at the given coordinates.
  const patchAllAt = (
    prev: Record<WeeklyDayKey, DayBooking>,
    lat: number,
    lng: number,
    address: string,
  ): Record<WeeklyDayKey, DayBooking> => {
    const fix = (loc: MapLocation): MapLocation =>
      loc.pinned && loc.lat === lat && loc.lng === lng ? { ...loc, address } : loc;
    const next = { ...prev };
    for (const key of WEEK_DAY_KEYS) {
      next[key] = {
        ...prev[key],
        pickup: fix(prev[key].pickup),
        dropoff: fix(prev[key].dropoff),
      };
    }
    return next;
  };

  const updateBooking = (day: WeeklyDayKey, patch: Partial<DayBooking>) => {
    setBookings(prev => ({ ...prev, [day]: { ...prev[day], ...patch } }));
  };

  const updateLocation = (day: WeeklyDayKey, which: 'pickup' | 'dropoff', loc: MapLocation) => {
    setBookings(prev => ({ ...prev, [day]: { ...prev[day], [which]: loc } }));
  };

  const toggleDay = (day: WeeklyDayKey) => {
    setError(null);
    if (enabled[day]) {
      setEnabled(prev => ({ ...prev, [day]: false }));
      return;
    }
    const activeCount = WEEK_DAY_KEYS.filter(k => enabled[k]).length;
    if (activeCount >= MAX_DAYS) {
      setError(`You can select up to ${MAX_DAYS} days per week.`);
      return;
    }
    // Default unpinned pickup/dropoff to the employee's home address.
    setBookings(prev => {
      const b = prev[day];
      const prefill = (loc: MapLocation) => (loc.pinned ? loc : home ?? loc);
      return { ...prev, [day]: { ...b, pickup: prefill(b.pickup), dropoff: prefill(b.dropoff) } };
    });
    setEnabled(prev => ({ ...prev, [day]: true }));
  };

  const validateDay = (b: DayBooking): string | null => {
    if (!isNightShift(b.shiftStart) || !isNightShift(b.shiftEnd)) return 'Pick a shift time between 10 PM and 6 AM.';
    if (b.shiftEnd === b.shiftStart) return 'Shift start and end must be different (overnight shifts run into the next day).';
    if (!b.pickup.pinned) return 'Set the pickup location.';
    if (!b.dropoff.pinned) return 'Set the dropoff location.';
    return null;
  };

  const buildPayload = (): WeeklyRequestPayload => {
    const payload: WeeklyRequestPayload = {};
    for (const key of WEEK_DAY_KEYS) {
      if (!enabled[key]) continue;
      const b = bookings[key];
      payload[key] = {
        shift_start_time: b.shiftStart,
        shift_end_time: b.shiftEnd,
        pickup_lat: b.pickup.lat,
        pickup_lng: b.pickup.lng,
        drop_lat: b.dropoff.lat,
        drop_lng: b.dropoff.lng,
      };
    }
    return payload;
  };

  const handleSubmit = async () => {
    setError(null);
    const active = WEEK_DAY_KEYS.filter(k => enabled[k]);
    if (active.length === 0) {
      setError('Select at least one day of the week.');
      return;
    }
    if (!weekly) return;
    for (const key of active) {
      const err = validateDay(bookings[key]);
      if (err) {
        setError(`${labelFor(weekly, key).full}: ${err}`);
        return;
      }
    }

    setIsSubmitting(true);
    try {
      const saved = await weeklyRequestApi.save(buildPayload());
      submittedDays.current = active.filter(k => saved.week[k]?.pickup);
      setStep('success');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit request');
    } finally {
      setIsSubmitting(false);
    }
  };

  const backToForm = async () => {
    setError(null);
    try {
      const view = await weeklyRequestApi.current();
      setWeekly(view);
      const hasSaved = WEEK_DAY_KEYS.some(k => view.week[k]?.pickup);
      setEditMode(hasSaved);
      const nextBookings = {} as Record<WeeklyDayKey, DayBooking>;
      const nextEnabled = {} as Record<WeeklyDayKey, boolean>;
      for (const key of WEEK_DAY_KEYS) {
        nextBookings[key] = dayFromView(view.week[key] ?? null, home);
        nextEnabled[key] = hasSaved ? !!view.week[key]?.pickup : false;
      }
      setBookings(nextBookings);
      setEnabled(nextEnabled);
    } catch {
      /* keep current state */
    }
    setStep('form');
  };

  if (loading) {
    return (
      <Sidebar role="employee">
        <div className="p-8 flex items-center justify-center min-h-[50vh]">
          <div className="w-8 h-8 border-2 border-[#14B8A6]/30 border-t-[#14B8A6] rounded-full animate-spin" />
        </div>
      </Sidebar>
    );
  }

  if (!weekly) {
    return (
      <Sidebar role="employee">
        <div className="p-8 max-w-2xl mx-auto">
          <h1 className="text-2xl font-bold text-foreground mb-2" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
            Pickup & Dropoff Request
          </h1>
          <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/8 px-5 py-4">
            <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm text-red-600 dark:text-red-400">{error ?? 'Could not load the request window.'}</p>
              <button
                onClick={() => window.location.reload()}
                className="mt-3 text-xs text-sky-600 dark:text-sky-400 hover:text-sky-500 dark:hover:text-sky-300 font-medium"
              >
                Retry
              </button>
            </div>
          </div>
        </div>
      </Sidebar>
    );
  }

  // ── Success screen ──────────────────────────────────────────────────────────
  if (step === 'success') {
    const dayNames = submittedDays.current.map(k => labelFor(weekly, k).full);
    return (
      <Sidebar role="employee">
        <div className="p-8 max-w-2xl mx-auto flex flex-col items-center justify-center min-h-[70vh]">
          <div className="rounded-2xl border border-emerald-500/20 bg-emerald-500/8 p-10 text-center max-w-md">
            <div className="w-16 h-16 rounded-full bg-emerald-500/15 border border-emerald-500/20 flex items-center justify-center mx-auto mb-5">
              <CheckCircle className="w-8 h-8 text-emerald-600 dark:text-emerald-400" />
            </div>
            <h2 className="text-2xl font-bold text-foreground mb-3" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Request Saved{editMode ? ' — Updated' : ''}!
            </h2>
            <p className="text-foreground text-sm leading-relaxed mb-2">
              Your pickup &amp; dropoff request for{' '}
              <span className="text-foreground font-medium">{dayNames.join(', ')}</span> has been submitted.
            </p>
            <p className="text-xs text-muted-foreground mb-6">
              Only one request counts per week — editing it before the deadline replaces it.
            </p>
            <button
              onClick={backToForm}
              className="px-6 py-2.5 rounded-lg bg-primary hover:opacity-90 text-primary-foreground text-sm font-semibold transition"
            >
              View My Request
            </button>
          </div>
        </div>
      </Sidebar>
    );
  }

  const windowOpen = weekly?.open ?? false;
  const countdownMs = windowOpen
    ? new Date(weekly!.window.closes).getTime() - now.getTime()
    : new Date(weekly!.window.next_open).getTime() - now.getTime();
  const activeCount = WEEK_DAY_KEYS.filter(k => enabled[k]).length;

  // ── Main form ───────────────────────────────────────────────────────────────
  return (
    <Sidebar role="employee">
      <div className="p-6 max-w-6xl mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-foreground mb-1" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
            Pickup & Dropoff Request
          </h1>
          <p className="text-muted-foreground text-sm">
            Weekly request for next week{' '}
            <span className="text-foreground">
              {labelFor(weekly, 'sun').short}, {labelFor(weekly, 'sun').label} →{' '}
              {labelFor(weekly, 'sat').short}, {labelFor(weekly, 'sat').label}
            </span>{' '}
            — select the days you'll come to the office.
          </p>
          <p className="text-xs text-muted-foreground mt-1">
            {activeCount} of {MAX_DAYS} days selected. Pickup &amp; dropoff default to your home address — adjust them
            on the map if needed.
          </p>
        </div>

        {/* Window banner */}
        {windowOpen ? (
          <div className="flex items-start gap-3 rounded-xl border border-sky-500/15 bg-sky-500/8 px-5 py-4 mb-6">
            <Info className="w-4 h-4 text-sky-600 dark:text-sky-400 mt-0.5 flex-shrink-0" />
            <div className="text-xs text-sky-600/80 dark:text-sky-400/80">
              <p>
                <span className="font-semibold text-sky-600 dark:text-sky-400">Request window is open.</span> Submit by{' '}
                <span className="font-semibold text-sky-600 dark:text-sky-400">Saturday 11:59 PM</span>. Time left:{' '}
                <span className="font-mono text-sky-600 dark:text-sky-400">{fmtCountdown(countdownMs)}</span>
              </p>
              {editMode && (
                <p className="mt-1 text-amber-600/80 dark:text-amber-400/80 flex items-center gap-1.5">
                  <Pencil className="w-3 h-3" /> You already have a request for this week — any change replaces it.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3 rounded-xl border border-border bg-muted px-5 py-4 mb-6">
            <Lock className="w-4 h-4 text-muted-foreground mt-0.5 flex-shrink-0" />
            <div className="text-xs text-foreground">
              <p>
                <span className="font-semibold text-foreground">Requests are closed.</span> New requests are only accepted
                on Friday &amp; Saturday, until Saturday 11:59 PM.
              </p>
              <p className="mt-1">
                Next window opens in{' '}
                <span className="font-mono text-foreground">{fmtCountdown(countdownMs)}</span>
                {editMode && <span className="text-amber-600/80 dark:text-amber-400/80"> — your saved request is shown below.</span>}
              </p>
            </div>
          </div>
        )}

        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/8 px-5 py-4 mb-6">
            <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-red-600 dark:text-red-400/90">{error}</p>
          </div>
        )}

        <fieldset disabled={!windowOpen} className={windowOpen ? '' : 'opacity-60 pointer-events-none'}>
          {/* Day toggles — all 7 days of next week */}
          <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-3 mb-6">
            {WEEK_DAY_KEYS.map(key => {
              const label = labelFor(weekly, key);
              return (
                <button
                  key={key}
                  type="button"
                  onClick={() => toggleDay(key)}
                  className={`rounded-xl border px-3 py-3 text-left transition-all ${
                    enabled[key]
                      ? 'bg-sky-500/15 border-sky-500/40'
                      : 'border-border bg-muted hover:border-foreground/20'
                  }`}
                >
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <p className={`text-sm font-bold ${enabled[key] ? 'text-sky-600 dark:text-sky-400' : 'text-muted-foreground'}`}>
                        {label.short}
                      </p>
                      <p className="text-xs mt-0.5 text-muted-foreground">{label.label}</p>
                    </div>
                    <div className={`w-4 h-4 rounded border flex-shrink-0 ${enabled[key] ? 'bg-sky-400 border-sky-300' : 'border-border'}`} />
                  </div>
                </button>
              );
            })}
          </div>

          {(enabled.sun || enabled.mon || enabled.tue || enabled.wed || enabled.thu || enabled.fri || enabled.sat) && (
            <div className="space-y-6">
              {WEEK_DAY_KEYS.filter(key => enabled[key]).map(key => {
                const b = bookings[key];
                const label = labelFor(weekly, key);
                return (
                  <div key={key} className="rounded-xl border border-border bg-card p-6">
                    <h3
                      className="text-base font-semibold text-foreground mb-5 flex items-center gap-2"
                      style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}
                    >
                      <CalendarDays className="w-4 h-4 text-sky-600 dark:text-sky-400" />
                      {label.full}, {label.label} — Shift Times
                    </h3>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
                      <div>
                        <label className="block text-xs text-muted-foreground mb-2 uppercase tracking-wider">
                          Shift Start Time
                        </label>
                        <div className="relative">
                          <Clock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                          <select
                            value={b.shiftStart}
                            onChange={e => updateBooking(key, { shiftStart: e.target.value })}
                            className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground text-sm focus:outline-none focus:border-sky-500/40 transition"
                          >
                            {SHIFT_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                        </div>
                      </div>
                      <div>
                        <label className="block text-xs text-muted-foreground mb-2 uppercase tracking-wider">
                          Shift End Time
                        </label>
                        <div className="relative">
                          <Clock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                          <select
                            value={b.shiftEnd}
                            onChange={e => updateBooking(key, { shiftEnd: e.target.value })}
                            className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground text-sm focus:outline-none focus:border-sky-500/40 transition"
                          >
                            {SHIFT_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
                          </select>
                        </div>
                      </div>
                    </div>

                    <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
                      <div className="rounded-xl border border-sky-500/15 bg-muted p-5">
                        <MapLocationPicker
                          title="Pickup Location"
                          description="Where the bus picks you up on the way to the office."
                          accentHex="#0EA5E9"
                          value={b.pickup}
                          onChange={loc => updateLocation(key, 'pickup', loc)}
                          origin={OFFICE_MARKER}
                        />
                      </div>
                      <div className="rounded-xl border border-emerald-500/15 bg-muted p-5">
                        <MapLocationPicker
                          title="Dropoff Location"
                          description="Where the bus drops you off after your shift."
                          accentHex="#10B981"
                          value={b.dropoff}
                          onChange={loc => updateLocation(key, 'dropoff', loc)}
                          origin={OFFICE_MARKER}
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}

          <div className="mt-8 flex justify-end">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={isSubmitting || !windowOpen}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-primary hover:opacity-90 text-primary-foreground font-semibold transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isSubmitting ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  {editMode ? 'Update Week Request' : 'Submit Week Request'}
                </>
              )}
            </button>
          </div>
        </fieldset>
      </div>
    </Sidebar>
  );
};
