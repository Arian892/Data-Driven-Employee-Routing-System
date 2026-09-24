import React, { useEffect, useRef, useState } from 'react';
import { Sidebar } from '../shared/Sidebar';
import { Send, CheckCircle, Info, AlertTriangle, Clock, Zap, Lock, CalendarDays, Pencil } from 'lucide-react';
import { MapLocationPicker, MapLocation } from './MapLocationPicker';
import { OFFICE_LOCATION } from '../../data/mockData';
import { adhocRequestApi, employeeApi } from '../../services/transportApi';
import type { AdhocRequestView } from '../../types/api';

// Shift options — overnight, 10 PM → 6 AM, one hour apart.
const NIGHT_SHIFT_OPTIONS = ['22:00', '23:00', '00:00', '01:00', '02:00', '03:00', '04:00', '05:00', '06:00'];

const DHAKA_CENTER: [number, number] = [23.7808, 90.4043];

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

const fmtCountdown = (ms: number): string => {
  if (ms <= 0) return '0m 0s';
  const s = Math.floor(ms / 1000);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${m}m ${sec}s`;
  return `${m}m ${sec}s`;
};

export const AdhocRequestForm: React.FC = () => {
  const [adhoc, setAdhoc] = useState<AdhocRequestView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [shiftStart, setShiftStart] = useState('22:00');
  const [shiftEnd, setShiftEnd] = useState('06:00');
  const [pickup, setPickup] = useState<MapLocation>(blankLocation());
  const [dropoff, setDropoff] = useState<MapLocation>(blankLocation());
  const [hasSaved, setHasSaved] = useState(false);
  const [now, setNow] = useState<Date>(() => new Date());
  const [step, setStep] = useState<'form' | 'success'>('form');
  const updatedRef = useRef(false);

  // Live countdown tick.
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  // Load the employee's home address + today's ad-hoc window / saved request.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [profile, view] = await Promise.all([
          employeeApi.getProfile(),
          adhocRequestApi.current(),
        ]);
        if (cancelled) return;

        let homeLoc: MapLocation | null = null;
        if (profile.home_lat != null && profile.home_lng != null) {
          homeLoc = locFromCoords(profile.home_lat, profile.home_lng);
          if (!cachedHomeAddress(profile.home_lat, profile.home_lng)) {
            reverseGeocode(profile.home_lat, profile.home_lng).then(addr => {
              if (cancelled) return;
              setPickup(cur =>
                cur.pinned && cur.lat === profile.home_lat && cur.lng === profile.home_lng ? { ...cur, address: addr } : cur,
              );
              setDropoff(cur =>
                cur.pinned && cur.lat === profile.home_lat && cur.lng === profile.home_lng ? { ...cur, address: addr } : cur,
              );
            });
          }
        }

        setAdhoc(view);
        const existing = view.existing ?? {};
        if (existing.pickup) {
          setShiftStart((existing.pickup.shift_start_time ?? '22:00').slice(0, 5));
        }
        if (existing.dropoff) {
          setShiftEnd((existing.dropoff.shift_end_time ?? '06:00').slice(0, 5));
        }
        const prefillPickup = existing.pickup
          ? locFromCoords(existing.pickup.pickup_lat, existing.pickup.pickup_lng)
          : homeLoc ?? blankLocation();
        const prefillDropoff = existing.dropoff
          ? locFromCoords(existing.dropoff.drop_lat, existing.dropoff.drop_lng)
          : homeLoc ?? blankLocation();
        setPickup(prefillPickup);
        setDropoff(prefillDropoff);
        setHasSaved(!!existing.pickup || !!existing.dropoff);

        // Upgrade any prefilled saved locations that only have coordinates.
        for (const loc of [prefillPickup, prefillDropoff]) {
          if (loc.pinned && !cachedHomeAddress(loc.lat, loc.lng)) {
            reverseGeocode(loc.lat, loc.lng).then(addr => {
              if (cancelled) return;
              const upgrade = (cur: MapLocation) =>
                cur.pinned && cur.lat === loc.lat && cur.lng === loc.lng ? { ...cur, address: addr } : cur;
              setPickup(upgrade);
              setDropoff(upgrade);
            });
          }
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Failed to load ad-hoc request');
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, []);

  const validate = (): string | null => {
    if (shiftEnd === shiftStart) return 'Shift start and end must be different (overnight shifts run into the next day).';
    if (!pickup.pinned) return 'Set the pickup location.';
    if (!dropoff.pinned) return 'Set the dropoff location.';
    return null;
  };

  const handleSubmit = async () => {
    setError(null);
    const err = validate();
    if (err) {
      setError(err);
      return;
    }

    updatedRef.current = hasSaved;
    setIsSubmitting(true);
    try {
      const saved = await adhocRequestApi.save({
        shift_start_time: shiftStart,
        shift_end_time: shiftEnd,
        pickup_lat: pickup.lat,
        pickup_lng: pickup.lng,
        drop_lat: dropoff.lat,
        drop_lng: dropoff.lng,
      });
      setAdhoc(saved);
      setHasSaved(true);
      setStep('success');
    } catch (err2) {
      setError(err2 instanceof Error ? err2.message : 'Failed to submit ad-hoc request');
    } finally {
      setIsSubmitting(false);
    }
  };

  const backToForm = async () => {
    setError(null);
    try {
      const view = await adhocRequestApi.current();
      setAdhoc(view);
      setHasSaved(!!view.existing?.pickup || !!view.existing?.dropoff);
    } catch {
      /* keep current state */
    }
    setStep('form');
  };

  if (loading) {
    return (
      <Sidebar role="employee">
        <div className="p-8 flex items-center justify-center min-h-[50vh]">
          <div className="w-8 h-8 border-2 border-amber-500/30 border-t-amber-400 rounded-full animate-spin" />
        </div>
      </Sidebar>
    );
  }

  if (!adhoc) {
    return (
      <Sidebar role="employee">
        <div className="p-8 max-w-2xl mx-auto">
          <h1 className="text-2xl font-bold text-foreground mb-2" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
            Ad-hoc Request
          </h1>
          <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/8 px-5 py-4">
            <AlertTriangle className="w-4 h-4 text-red-600 dark:text-red-400 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm text-red-600 dark:text-red-400">{error ?? 'Could not load the ad-hoc window.'}</p>
              <button onClick={() => window.location.reload()} className="mt-3 text-xs text-amber-400 hover:text-amber-300 font-medium">
                Retry
              </button>
            </div>
          </div>
        </div>
      </Sidebar>
    );
  }

  if (step === 'success') {
    return (
      <Sidebar role="employee">
        <div className="p-8 max-w-2xl mx-auto flex flex-col items-center justify-center min-h-[70vh]">
          <div className="rounded-2xl border border-amber-500/20 bg-amber-500/8 p-10 text-center max-w-md">
            <div className="w-16 h-16 rounded-full bg-amber-500/15 border border-amber-500/20 flex items-center justify-center mx-auto mb-5">
              <CheckCircle className="w-8 h-8 text-amber-400" />
            </div>
            <h2 className="text-2xl font-bold text-foreground mb-3" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Ad-hoc Submitted{updatedRef.current ? ' — Updated' : ''}!
            </h2>
            <p className="text-foreground text-sm leading-relaxed mb-2">
              Your ad-hoc request for{' '}
              <span className="text-foreground font-medium">
                {new Date(`${adhoc.service_date}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })}
              </span>{' '}
              is pending.
            </p>
            <p className="text-xs text-muted-foreground mb-6">Your route updates automatically after 10 PM tonight.</p>
            <button onClick={backToForm} className="px-6 py-2.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-black text-sm font-semibold transition">
              View My Request
            </button>
          </div>
        </div>
      </Sidebar>
    );
  }

  const open = adhoc.open;
  const countdownMs = new Date(adhoc.cutoff).getTime() - now.getTime();

  return (
    <Sidebar role="employee">
      <div className="p-6 max-w-6xl mx-auto">
        <div className="mb-8">
          <div className="flex items-center gap-3 mb-1">
            <div className="w-8 h-8 rounded-lg bg-amber-500/15 border border-amber-500/20 flex items-center justify-center">
              <Zap className="w-4 h-4 text-amber-400" />
            </div>
            <h1 className="text-3xl font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Ad-hoc Request
            </h1>
          </div>
          <p className="text-muted-foreground text-sm ml-11">For same-day transport changes — request only on the day itself, before 7 PM.</p>
        </div>

        {open ? (
          <div className="flex items-start gap-3 rounded-xl border border-amber-500/15 bg-amber-500/6 px-5 py-4 mb-6">
            <Info className="w-4 h-4 text-amber-400 mt-0.5 flex-shrink-0" />
            <div className="text-xs text-amber-400/80">
              <p>
                <span className="font-semibold text-amber-400">Ad-hoc is open for today.</span> Submit by{' '}
                <span className="font-semibold text-amber-400">7:00 PM</span> (3 hours before the 10 PM shift). Time left:{' '}
                <span className="font-mono text-amber-400">{fmtCountdown(countdownMs)}</span>
              </p>
              {hasSaved && (
                <p className="mt-1 flex items-center gap-1.5">
                  <Pencil className="w-3 h-3" /> You already have an ad-hoc request for today — submitting again replaces it.
                </p>
              )}
            </div>
          </div>
        ) : (
          <div className="flex items-start gap-3 rounded-xl border border-border bg-muted px-5 py-4 mb-6">
            <Lock className="w-4 h-4 text-muted-foreground mt-0.5 flex-shrink-0" />
            <div className="text-xs text-foreground">
              <p>
                <span className="font-semibold text-foreground">Ad-hoc is closed.</span> {adhoc.reason}
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

        <fieldset disabled={!open} className={open ? '' : 'opacity-60 pointer-events-none'}>
          <div className="rounded-xl border border-border bg-card p-6">
            <h3
              className="text-base font-semibold text-foreground mb-5 flex items-center gap-2"
              style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}
            >
              <CalendarDays className="w-4 h-4 text-amber-400" />
              {new Date(`${adhoc.service_date}T00:00:00`).toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' })} — Shift Times
            </h3>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5 mb-6">
              <div>
                <label className="block text-xs text-muted-foreground mb-2 uppercase tracking-wider">Shift Start Time</label>
                <div className="relative">
                  <Clock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <select
                    value={shiftStart}
                    onChange={e => setShiftStart(e.target.value)}
                    className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground text-sm focus:outline-none focus:border-amber-500/40 transition"
                  >
                    {NIGHT_SHIFT_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
                  </select>
                </div>
              </div>
              <div>
                <label className="block text-xs text-muted-foreground mb-2 uppercase tracking-wider">Shift End Time</label>
                <div className="relative">
                  <Clock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <select
                    value={shiftEnd}
                    onChange={e => setShiftEnd(e.target.value)}
                    className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground text-sm focus:outline-none focus:border-amber-500/40 transition"
                  >
                    {NIGHT_SHIFT_OPTIONS.map(t => <option key={t} value={t}>{t}</option>)}
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
                  value={pickup}
                  onChange={setPickup}
                  origin={OFFICE_MARKER}
                />
              </div>
              <div className="rounded-xl border border-emerald-500/15 bg-muted p-5">
                <MapLocationPicker
                  title="Dropoff Location"
                  description="Where the bus drops you off after your shift."
                  accentHex="#10B981"
                  value={dropoff}
                  onChange={setDropoff}
                  origin={OFFICE_MARKER}
                />
              </div>
            </div>
          </div>

          <div className="mt-8 flex justify-end">
            <button
              type="button"
              onClick={handleSubmit}
              disabled={isSubmitting || !open}
              className="flex items-center gap-2 px-6 py-3 rounded-xl bg-amber-500 hover:bg-amber-400 text-black font-semibold transition disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {isSubmitting ? (
                <>
                  <div className="w-4 h-4 border-2 border-black/30 border-t-black rounded-full animate-spin" />
                  Saving...
                </>
              ) : (
                <>
                  <Send className="w-4 h-4" />
                  {hasSaved ? 'Update Ad-hoc Request' : 'Submit Ad-hoc Request'}
                </>
              )}
            </button>
          </div>
        </fieldset>
      </div>
    </Sidebar>
  );
};
