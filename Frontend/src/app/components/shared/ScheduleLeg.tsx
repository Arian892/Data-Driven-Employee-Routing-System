import React from 'react';
import { ArrowUpRight, ArrowDownLeft, MapPin, Clock, User as UserIcon, Car } from 'lucide-react';
import { AddressText } from './AddressText';
import { OFFICE_LOCATION } from '../../data/mockData';
import type { RouteGeometry, ScheduleLeg } from '../../types/api';

/** Shared by any screen that shows an employee's pickup/dropoff route
 * (My Requests, My Profile's "Today's Route") so the two stay in sync. */

/** Small caption under a route map explaining what each marker color means —
 * without it, the highlighted "your stop" pin just looks like a random
 * inconsistent color rather than an intentional callout. */
// Pickup and dropoff are visually distinct everywhere else in the app
// (ScheduleLegDetails uses emerald/violet for the same distinction) — the "S"
// marker follows that same pickup/dropoff split so it reads as "this leg's
// vehicle" rather than a generic, identical dot regardless of direction.
export const START_MARKER_COLOR: Record<'pickup' | 'dropoff', string> = {
  pickup: '#0D9488', // teal
  dropoff: '#7C3AED', // violet
};

export const MapLegend: React.FC<{ showMine?: boolean; routeType?: 'pickup' | 'dropoff' }> = ({ showMine = true, routeType }) => (
  <div className="flex items-center flex-wrap gap-x-4 gap-y-1 mt-2.5 text-xs text-slate-300">
    <span className="flex items-center gap-1.5">
      <span
        className="w-2.5 h-2.5 rounded-full border border-white/40 flex-shrink-0"
        style={{ backgroundColor: routeType ? START_MARKER_COLOR[routeType] : '#64748B' }}
      /> Vehicle start
    </span>
    <span className="flex items-center gap-1.5">
      <span className="w-2.5 h-2.5 rounded-full bg-slate-400 border border-white/40 flex-shrink-0" /> Stop
    </span>
    {showMine && (
      <span className="flex items-center gap-1.5">
        <span className="w-2.5 h-2.5 rounded-full bg-amber-400 border border-white/40 flex-shrink-0" /> Your stop
      </span>
    )}
    <span className="flex items-center gap-1.5">
      <span className="w-2.5 h-2.5 rounded-full bg-white border border-white/40 flex-shrink-0" /> Office
    </span>
  </div>
);

export const coordinateLabel = (lat?: number | null, lng?: number | null) => {
  if (lat == null || lng == null) return 'No location set';
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
};

// Matches the solver's own synthetic stop-name keys, e.g. "Ad-hoc (Md.
// Shamiul Alam)" or "Home (Md. Shamiul Alam)" (solver.py's `_place_on`).
// These are internal identifiers for a door-to-door stop, not place names —
// checked by pattern as well as the `is_adhoc` flag, since older/other-source
// rows can carry the same synthetic name without that flag set.
const SYNTHETIC_STOP_NAME = /^(ad-hoc|home)\s*\(.+\)$/i;

/** True when `stop_name` shouldn't be shown as a place — either the row says
 * so (`is_adhoc`) or the name itself matches the solver's synthetic pattern. */
export const isSyntheticStopName = (name?: string | null, isAdhoc?: boolean | null): boolean =>
  Boolean(isAdhoc) || (!!name && SYNTHETIC_STOP_NAME.test(name.trim()));

export type RouteMarker = {
  position: [number, number];
  label: string;
  color?: string;
  code?: string | number;
  variant?: 'stop' | 'office' | 'mine' | 'start';
};

// Rough metre distance for small separations — flat-earth approximation is
// plenty accurate at the scale (tens of metres) this check cares about.
const roughMetres = (a: [number, number], b: [number, number]): number => {
  const dLat = (a[0] - b[0]) * 111_320;
  const dLng = (a[1] - b[1]) * 111_320 * Math.cos((a[0] * Math.PI) / 180);
  return Math.sqrt(dLat * dLat + dLng * dLng);
};

/** The vehicle's own position before it visits anything on this leg — not a
 * rider stop, but a real driven leg (a "deadhead"): for pickup, the car's
 * parking/current spot before it reaches the first passenger; for dropoff,
 * likewise before the car reaches the office. The solver puts this position
 * first in the coordinates it hands the routing engine (see solver.py's
 * pickup/dropoff `full` lists), so `route_geometry[0]` — already stored on
 * every route — IS that position; no separate column needed to show it.
 *
 * In practice the vehicle is very often already parked at (or right next to)
 * a stop, and on a dense multi-stop route that can mean several stops sit
 * within a few metres of each other and of the vehicle's own start — left
 * alone, Leaflet just stacks pins exactly on top of one another and "S" is
 * never actually visible. So this walks outward along 8 compass directions
 * at increasing radius until it finds a spot clear of every other marker on
 * this leg (never far enough to misrepresent the real location — the whole
 * search stays within ~50m). */
const vehicleStartMarker = (
  geometry: RouteGeometry | null | undefined,
  others: RouteMarker[],
  routeType: 'pickup' | 'dropoff',
): RouteMarker | null => {
  const first = geometry?.[0];
  if (!first) return null;

  const clashesWith = (pos: [number, number]) => others.some(m => roughMetres(pos, m.position) < 25);

  let position: [number, number] = [first[0], first[1]];
  if (clashesWith(position)) {
    const metresPerDegLat = 111_320;
    const metresPerDegLng = 111_320 * Math.cos((position[0] * Math.PI) / 180);
    outer: for (const radius of [20, 35, 50]) {
      for (let i = 0; i < 8; i++) {
        const angle = (i / 8) * 2 * Math.PI;
        const candidate: [number, number] = [
          position[0] + (radius * Math.cos(angle)) / metresPerDegLat,
          position[1] + (radius * Math.sin(angle)) / metresPerDegLng,
        ];
        if (!clashesWith(candidate)) {
          position = candidate;
          break outer;
        }
      }
    }
    // Every radius tried and still clashing (very dense cluster) — fall back
    // to the first, smallest offset rather than leaving it unmoved.
    if (clashesWith(position) && position[0] === first[0] && position[1] === first[1]) {
      position = [first[0] + 20 / 111_320, first[1]];
    }
  }
  return { position, label: 'Vehicle Start', code: 'S', variant: 'start', color: START_MARKER_COLOR[routeType] };
};

/** Builds the sequentially-numbered marker list for one leg's map: every
 * stop on the route (not just this employee's own), numbered 1..N in
 * visiting order, with the viewing employee's own stop highlighted.
 *
 * The office itself is never a stored `route_stop` row (the solver treats it
 * as the fixed start/end of the drive, not a scheduled stop — see
 * routing/solver.py), so it's added here as its own unique marker: at the
 * END for pickup (the route drives everyone to the office last) and at the
 * START for dropoff (the vehicle departs the office before its first stop).
 * The "S" marker for the vehicle's own starting position comes before either
 * — see `vehicleStartMarker`. */
export const buildLegMarkers = (leg: ScheduleLeg | undefined | null): RouteMarker[] => {
  if (!leg) return [];

  const stops = [...(leg.stops ?? [])].sort((a, b) => a.sequence_order - b.sequence_order);
  const numbered: RouteMarker[] = stops.map((s, idx) => ({
    position: [s.latitude, s.longitude],
    label: s.is_mine ? 'Your Stop' : (!isSyntheticStopName(s.stop_name, s.is_adhoc) && s.stop_name) || `Stop ${s.sequence_order}`,
    code: idx + 1,
    variant: s.is_mine ? 'mine' : 'stop',
  }));

  const office: RouteMarker = {
    position: [OFFICE_LOCATION.latitude, OFFICE_LOCATION.longitude],
    label: 'Office',
    variant: 'office',
  };
  const start = vehicleStartMarker(leg.route_geometry, [...numbered, office], leg.route_type === 'dropoff' ? 'dropoff' : 'pickup');
  const startList = start ? [start] : [];

  if (numbered.length === 0) return [...startList, office];
  return leg.route_type === 'dropoff'
    ? [...startList, office, ...numbered]
    : [...startList, ...numbered, office];
};

/** Same numbering/office convention as `buildLegMarkers`, for the driver's
 * view — every stop on the route (the driver isn't a passenger, so there's
 * no "mine" stop to highlight), numbered 1..N by the stop's own
 * `sequence_order` rather than array position, so the pin always matches
 * whatever number is shown next to it in a stop list. */
export const buildDriverStopMarkers = (
  stops: Array<{
    latitude?: number | null;
    longitude?: number | null;
    sequence_order?: number | null;
    stop_name?: string | null;
    is_adhoc?: boolean | null;
  }>,
  routeType?: string | null,
  routeGeometry?: RouteGeometry | null,
): RouteMarker[] => {
  const sorted = stops
    .filter((s): s is typeof s & { latitude: number; longitude: number } => s.latitude != null && s.longitude != null)
    .sort((a, b) => (a.sequence_order ?? 0) - (b.sequence_order ?? 0));

  const numbered: RouteMarker[] = sorted.map(s => ({
    position: [s.latitude, s.longitude],
    label: (!isSyntheticStopName(s.stop_name, s.is_adhoc) && s.stop_name) || `Stop ${s.sequence_order ?? ''}`,
    code: s.sequence_order ?? undefined,
    variant: 'stop',
  }));

  const office: RouteMarker = {
    position: [OFFICE_LOCATION.latitude, OFFICE_LOCATION.longitude],
    label: 'Office',
    variant: 'office',
  };
  const start = vehicleStartMarker(routeGeometry, [...numbered, office], routeType === 'dropoff' ? 'dropoff' : 'pickup');
  const startList = start ? [start] : [];

  if (numbered.length === 0) return [...startList, office];
  return routeType === 'dropoff'
    ? [...startList, office, ...numbered]
    : [...startList, ...numbered, office];
};

/** One leg of the night's assignment — the ride in, or the ride home.
 *
 * An employee has both on the same service date, so this renders once per leg
 * rather than collapsing the night to a single stop. */
export const ScheduleLegDetails: React.FC<{ leg: ScheduleLeg }> = ({ leg }) => {
  const isPickup = leg.route_type === 'pickup';
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        {isPickup
          ? <ArrowUpRight className="w-4 h-4 text-emerald-600 dark:text-emerald-400 flex-shrink-0" />
          : <ArrowDownLeft className="w-4 h-4 text-violet-600 dark:text-violet-400 flex-shrink-0" />}
        <p className="text-sm text-foreground">
          {isPickup ? 'Ride to office' : 'Ride home'} · stop {leg.stop.sequence_order}
          {leg.shift_time ? ` · shift ${leg.shift_time.slice(0, 5)}` : ''}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <MapPin className="w-4 h-4 flex-shrink-0" style={{ color: '#14B8A6' }} />
        <div className="text-sm">
          {/* A named stop is the solver's own label — for a shared main-road drop
              ("Agargaon Metro Station") that is the difference between the
              employee walking to the right place and expecting a door pickup.
              An *ad-hoc* stop's "name" is a synthetic key instead — literally
              "Ad-hoc (Md. Shamiul Alam)" / "Home (Md. Shamiul Alam)" — so for
              those, show the actual address instead of that internal label. */}
          {leg.stop.stop_name && !isSyntheticStopName(leg.stop.stop_name, leg.stop.is_adhoc) ? (
            <p className="text-foreground">{leg.stop.stop_name}</p>
          ) : (
            <AddressText lat={leg.stop.latitude} lng={leg.stop.longitude} className="text-foreground" />
          )}
          {leg.stop.is_shared && (
            <p className="text-xs text-violet-600 dark:text-violet-300 mt-0.5">
              Shared drop point — walk from here to your home.
            </p>
          )}
          <p className="text-xs font-mono text-muted-foreground">{coordinateLabel(leg.stop.latitude, leg.stop.longitude)}</p>
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
      {leg.driver && (
        <div className="flex items-center gap-2">
          <UserIcon className="w-4 h-4 flex-shrink-0 text-muted-foreground" />
          <p className="text-sm text-foreground">
            Driver: {leg.driver.name}{leg.driver.phone ? ` · ${leg.driver.phone}` : ''}
          </p>
        </div>
      )}
      {leg.vehicle && (
        <div className="flex items-center gap-2">
          <Car className="w-4 h-4 flex-shrink-0 text-muted-foreground" />
          <p className="text-sm text-foreground">Vehicle: {leg.vehicle.plate_no ?? '—'}</p>
        </div>
      )}
    </div>
  );
};
