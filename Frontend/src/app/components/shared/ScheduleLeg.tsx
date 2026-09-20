import React from 'react';
import { ArrowUpRight, ArrowDownLeft, MapPin, Clock, User as UserIcon, Car } from 'lucide-react';
import { AddressText } from './AddressText';
import { OFFICE_LOCATION } from '../../data/mockData';
import type { ScheduleLeg } from '../../types/api';

/** Shared by any screen that shows an employee's pickup/dropoff route
 * (My Requests, My Profile's "Today's Route") so the two stay in sync. */

/** Small caption under a route map explaining what each marker color means —
 * without it, the highlighted "your stop" pin just looks like a random
 * inconsistent color rather than an intentional callout. */
export const MapLegend: React.FC<{ showMine?: boolean }> = ({ showMine = true }) => (
  <div className="flex items-center flex-wrap gap-x-4 gap-y-1 mt-2.5 text-xs text-slate-300">
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
  variant?: 'stop' | 'office' | 'mine';
};

/** Builds the sequentially-numbered marker list for one leg's map: every
 * stop on the route (not just this employee's own), numbered 1..N in
 * visiting order, with the viewing employee's own stop highlighted.
 *
 * The office itself is never a stored `route_stop` row (the solver treats it
 * as the fixed start/end of the drive, not a scheduled stop — see
 * routing/solver.py), so it's added here as its own unique marker: at the
 * END for pickup (the route drives everyone to the office last) and at the
 * START for dropoff (the vehicle departs the office before its first stop). */
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

  if (numbered.length === 0) return [office];
  return leg.route_type === 'dropoff' ? [office, ...numbered] : [...numbered, office];
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

  if (numbered.length === 0) return [office];
  return routeType === 'dropoff' ? [office, ...numbered] : [...numbered, office];
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
          ? <ArrowUpRight className="w-4 h-4 text-emerald-400 flex-shrink-0" />
          : <ArrowDownLeft className="w-4 h-4 text-violet-400 flex-shrink-0" />}
        <p className="text-sm text-slate-200">
          {isPickup ? 'Ride to office' : 'Ride home'} · stop {leg.stop.sequence_order}
          {leg.shift_time ? ` · shift ${leg.shift_time.slice(0, 5)}` : ''}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <MapPin className="w-4 h-4 text-sky-400 flex-shrink-0" />
        <div className="text-sm">
          {/* A named stop is the solver's own label — for a shared main-road drop
              ("Agargaon Metro Station") that is the difference between the
              employee walking to the right place and expecting a door pickup.
              An *ad-hoc* stop's "name" is a synthetic key instead — literally
              "Ad-hoc (Md. Shamiul Alam)" / "Home (Md. Shamiul Alam)" — so for
              those, show the actual address instead of that internal label. */}
          {leg.stop.stop_name && !isSyntheticStopName(leg.stop.stop_name, leg.stop.is_adhoc) ? (
            <p className="text-white">{leg.stop.stop_name}</p>
          ) : (
            <AddressText lat={leg.stop.latitude} lng={leg.stop.longitude} className="text-white" />
          )}
          {leg.stop.is_shared && (
            <p className="text-xs text-violet-300 mt-0.5">
              Shared drop point — walk from here to your home.
            </p>
          )}
          <p className="text-xs text-slate-400 font-mono">{coordinateLabel(leg.stop.latitude, leg.stop.longitude)}</p>
        </div>
      </div>
      {leg.stop.arrival_time && (
        <div className="flex items-center gap-2">
          <Clock className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <p className="text-sm text-slate-200">
            {isPickup ? 'Pickup at' : 'Dropoff at'}: {leg.stop.arrival_time}
          </p>
        </div>
      )}
      {leg.driver && (
        <div className="flex items-center gap-2">
          <UserIcon className="w-4 h-4 text-sky-400 flex-shrink-0" />
          <p className="text-sm text-slate-200">
            Driver: {leg.driver.name}{leg.driver.phone ? ` · ${leg.driver.phone}` : ''}
          </p>
        </div>
      )}
      {leg.vehicle && (
        <div className="flex items-center gap-2">
          <Car className="w-4 h-4 text-sky-400 flex-shrink-0" />
          <p className="text-sm text-slate-200">Vehicle: {leg.vehicle.plate_no ?? '—'}</p>
        </div>
      )}
    </div>
  );
};
