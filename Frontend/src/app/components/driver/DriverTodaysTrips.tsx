import React, { useEffect, useState } from 'react';
import { Sidebar } from '../shared/Sidebar';
import {
  Users, Clock, MapPin, CheckCircle, Navigation, Map,
  ChevronDown, ChevronUp, Car, Coffee, Play, Flag, CalendarDays,
  Loader2, AlertCircle, Route as RouteIcon,
} from 'lucide-react';
import { Switch } from '../ui/switch';
import { InteractiveMap } from '../shared/InteractiveMap';
import { AddressText } from '../shared/AddressText';
import { buildDriverStopMarkers, isSyntheticStopName, MapLegend } from '../shared/ScheduleLeg';
import { OFFICE_LOCATION } from '../../data/mockData';
import { driverApi } from '../../services/transportApi';
import type { DriverAssignmentRoute } from '../../types/api';

const todayISO = () => new Date().toISOString().split('T')[0];

const shiftDate = (base: string, delta: number) => {
  const d = new Date(`${base}T00:00:00`);
  d.setDate(d.getDate() + delta);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
};

const prettyDate = (iso: string) =>
  new Date(`${iso}T00:00:00`).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });

const statusInfo = (status?: string | null) => {
  if (status === 'Completed') return { label: 'Completed', dot: 'bg-emerald-500', text: 'text-emerald-700 dark:text-emerald-300' };
  if (status === 'InProgress') return { label: 'In Progress', dot: 'bg-[#14B8A6]', text: 'text-[#14B8A6]' };
  return { label: 'Not Started', dot: 'bg-slate-400', text: 'text-slate-500 dark:text-slate-400' };
};

export const DriverTodaysTrips: React.FC = () => {
  const [selectedDate, setSelectedDate] = useState(todayISO);
  const [routes, setRoutes] = useState<DriverAssignmentRoute[]>([]);
  const [expanded, setExpanded] = useState<number[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadRoutes = async (date: string) => {
    setLoading(true);
    setError(null);
    try {
      const data = await driverApi.getTodayAssignment(date);
      setRoutes(data.routes ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load assignments');
      setRoutes([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadRoutes(selectedDate);
  }, [selectedDate]);

  const toggleExpand = (id: number) => {
    setExpanded(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
  };

  const changeStatus = async (route: DriverAssignmentRoute, next: string) => {
    const assignmentId = route.assignment?.assignment_id;
    if (assignmentId == null) return;
    setError(null);
    try {
      if (next === 'InProgress') await driverApi.startAssignment(assignmentId);
      else await driverApi.completeAssignment(assignmentId);
      setRoutes(prev =>
        prev.map(r =>
          r.route_id === route.route_id && r.assignment
            ? { ...r, assignment: { ...r.assignment, status: next } }
            : r,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update trip status');
    }
  };

  const boardPassenger = async (route: DriverAssignmentRoute, stopId: number, pax: { employee_id?: number | null; boarded?: boolean | null }) => {
    if (pax.boarded || pax.employee_id == null) return;
    setError(null);
    try {
      await driverApi.boardPassenger(stopId, pax.employee_id);
      setRoutes(prev =>
        prev.map(r =>
          r.route_id === route.route_id
            ? {
                ...r,
                stops: r.stops.map(s =>
                  s.stop_id === stopId
                    ? {
                        ...s,
                        passengers: s.passengers.map(p =>
                          p.employee_id === pax.employee_id ? { ...p, boarded: true } : p,
                        ),
                      }
                    : s,
                ),
              }
            : r,
        ),
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to mark passenger boarded');
    }
  };

  const totalPassengers = routes.reduce((a, r) => a + r.stops.reduce((b, s) => b + (s.passengers?.length ?? 0), 0), 0);
  const boardedPassengers = routes.reduce(
    (a, r) => a + r.stops.reduce((b, s) => b + (s.passengers?.filter(p => p.boarded).length ?? 0), 0),
    0,
  );
  const completedCount = routes.filter(r => statusInfo(r.assignment?.status).label === 'Completed').length;
  const inProgressCount = routes.filter(r => statusInfo(r.assignment?.status).label === 'In Progress').length;
  const allDone = routes.length > 0 && completedCount === routes.length;
  const anyNotStarted = routes.some(r => statusInfo(r.assignment?.status).label === 'Not Started');

  return (
    <Sidebar role="driver">
      <div className="p-6 max-w-[1600px] mx-auto">
        <div className="flex items-center gap-4 pb-6 mb-6 border-b border-border">
          <div className="w-12 h-12 rounded-2xl bg-muted border border-border flex items-center justify-center flex-shrink-0">
            <Map className="w-6 h-6 text-foreground" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Today's Trips
            </h1>
            <p className="text-muted-foreground text-sm mt-0.5">{prettyDate(selectedDate)}</p>
          </div>
        </div>

        <div className="mb-8">
          {/* Date selector */}
          <div className="flex items-center gap-3 mt-4 flex-wrap">
            <div className="flex items-center gap-2">
              <button
                onClick={() => setSelectedDate(prev => shiftDate(prev, -1))}
                className="w-9 h-9 rounded-lg border border-border bg-muted hover:bg-border text-foreground transition flex items-center justify-center"
                aria-label="Previous day"
              >
                ‹
              </button>
              <button
                onClick={() => setSelectedDate(prev => shiftDate(prev, 1))}
                className="w-9 h-9 rounded-lg border border-border bg-muted hover:bg-border text-foreground transition flex items-center justify-center"
                aria-label="Next day"
              >
                ›
              </button>
            </div>
            <div className="flex items-center gap-2 rounded-lg border border-border bg-muted px-3 py-2">
              <CalendarDays className="w-4 h-4 text-muted-foreground" />
              <input
                type="date"
                value={selectedDate}
                onChange={e => e.target.value && setSelectedDate(e.target.value)}
                className="bg-transparent text-sm text-foreground focus:outline-none"
              />
            </div>
            {selectedDate !== todayISO() && (
              <button
                onClick={() => setSelectedDate(todayISO())}
                className="text-xs text-[#14B8A6] hover:text-[#2DD4BF] font-medium"
              >
                Back to today
              </button>
            )}
          </div>
        </div>

        {error && (
          <div className="flex items-start gap-3 rounded-xl border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/8 px-5 py-4 mb-6">
            <AlertCircle className="w-4 h-4 text-red-500 dark:text-red-400 mt-0.5 flex-shrink-0" />
            <p className="text-xs text-red-700 dark:text-red-300/90">{error}</p>
          </div>
        )}

        {allDone && (
          <div className="flex items-center gap-3 rounded-xl border border-emerald-200 dark:border-emerald-500/20 bg-emerald-50 dark:bg-emerald-500/8 px-5 py-4 mb-6">
            <CheckCircle className="w-5 h-5 text-emerald-600 dark:text-emerald-400" />
            <div>
              <p className="text-sm font-semibold text-emerald-700 dark:text-emerald-300">All trips completed!</p>
              <p className="text-xs text-muted-foreground mt-0.5">Return to your parking location. Have a safe drive!</p>
            </div>
          </div>
        )}

        {!allDone && !inProgressCount && anyNotStarted && routes.length > 0 && (
          <div className="flex items-center gap-3 rounded-xl border border-amber-200 dark:border-amber-500/15 bg-amber-50 dark:bg-amber-500/6 px-5 py-4 mb-6">
            <Coffee className="w-5 h-5 text-amber-600 dark:text-amber-400" />
            <div>
              <p className="text-sm font-semibold text-amber-700 dark:text-amber-300">Standby at Office</p>
              <p className="text-xs text-muted-foreground mt-0.5">Wait at the office departure bay until the next trip starts.</p>
            </div>
          </div>
        )}

        {/* Stats */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
          {[
            { label: 'Total Trips', value: routes.length, icon: RouteIcon, color: 'text-stone-400' },
            { label: 'Completed', value: completedCount, icon: CheckCircle, color: 'text-emerald-500' },
            { label: 'In Progress', value: inProgressCount, icon: Navigation, color: 'text-slate-500' },
            { label: 'Passengers', value: `${boardedPassengers}/${totalPassengers}`, icon: Users, color: 'text-amber-500' },
          ].map(stat => (
            <div key={stat.label} className="rounded-2xl bg-card border border-border px-5 py-4 shadow-sm">
              <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-1.5">{stat.label}</p>
              <p className="text-3xl font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>{stat.value}</p>
            </div>
          ))}
        </div>

        {/* Trips */}
        {loading ? (
          <div className="text-center py-20 text-muted-foreground">
            <Loader2 className="w-10 h-10 mx-auto mb-3 animate-spin opacity-30" />
            <p>Loading your routes...</p>
          </div>
        ) : routes.length === 0 ? (
          <div className="text-center py-20 text-muted-foreground rounded-xl border border-dashed border-border">
            <RouteIcon className="w-10 h-10 mx-auto mb-3 opacity-30" />
            <p className="text-sm text-muted-foreground">No route assigned for this day.</p>
            <p className="text-xs text-muted-foreground mt-1">Routes appear here once routing completes for the service week.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {routes.map(route => {
              const routeId = route.route_id ?? 0;
              const isOpen = expanded.includes(routeId);
              const info = statusInfo(route.assignment?.status);
              const orderedStops = route.stops.slice().sort((a, b) => (a.sequence_order ?? 0) - (b.sequence_order ?? 0));
              const firstStop = orderedStops[0];
              const totalPax = route.stops.reduce((a, s) => a + (s.passengers?.length ?? 0), 0);
              const boardedPax = route.stops.reduce((a, s) => a + (s.passengers?.filter(p => p.boarded).length ?? 0), 0);
              const isPickup = route.route_type === 'pickup';

              return (
                <div key={routeId} className="rounded-xl bg-card border border-border overflow-hidden shadow-sm">
                  {/* Header */}
                  <div
                    className="flex items-center justify-between px-5 py-4 cursor-pointer hover:bg-muted transition"
                    onClick={() => toggleExpand(routeId)}
                  >
                    <div className="flex items-center gap-4">
                      <div className="w-10 h-10 rounded-xl bg-muted border border-border flex items-center justify-center">
                        <Navigation className="w-5 h-5 text-foreground" />
                      </div>
                      <div>
                        <div className="flex items-center gap-2 mb-0.5">
                          <p className="text-sm font-semibold text-foreground capitalize">
                            {isPickup ? 'Pickup Route' : 'Dropoff Route'}
                            {route.zone_name ? ` · ${route.zone_name}` : ''}
                          </p>
                          <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-0.5 rounded-full bg-muted border border-border font-medium ${info.text}`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${info.dot}`} />
                            {info.label}
                          </span>
                        </div>
                        <div className="flex items-center gap-4 text-xs text-muted-foreground">
                          <span className="flex items-center gap-1"><Clock className="w-3 h-3" />{route.shift_time}</span>
                          <span className="flex items-center gap-1"><MapPin className="w-3 h-3" />{route.stops.length} stops</span>
                          <span className="flex items-center gap-1"><Users className="w-3 h-3" />{boardedPax}/{totalPax} boarded</span>
                          <span className="flex items-center gap-1">
                            <Car className="w-3 h-3" />
                            {route.total_distance_km != null ? `${route.total_distance_km} km` : '—'}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-3">
                      {info.label === 'Not Started' && route.assignment?.assignment_id != null && (
                        <button
                          onClick={e => { e.stopPropagation(); void changeStatus(route, 'InProgress'); }}
                          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-primary text-primary-foreground hover:opacity-90 text-xs font-semibold transition"
                        >
                          <Play className="w-3 h-3" />
                          Start
                        </button>
                      )}
                      {info.label === 'In Progress' && route.assignment?.assignment_id != null && (
                        <button
                          onClick={e => { e.stopPropagation(); void changeStatus(route, 'Completed'); }}
                          className="flex items-center gap-1.5 px-3 py-2 rounded-lg bg-emerald-500 hover:bg-emerald-600 text-white text-xs font-semibold transition"
                        >
                          <Flag className="w-3 h-3" />
                          Complete
                        </button>
                      )}
                      {isOpen ? <ChevronUp className="w-4 h-4 text-muted-foreground" /> : <ChevronDown className="w-4 h-4 text-muted-foreground" />}
                    </div>
                  </div>

                  {/* Expanded */}
                  {isOpen && (
                    <div className="border-t border-border p-5">
                      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
                        {/* Map */}
                        <div className="lg:col-span-3">
                          <p className="text-xs text-muted-foreground mb-3 uppercase tracking-wider">Route Map</p>
                          <InteractiveMap
                            center={firstStop ? [firstStop.latitude ?? OFFICE_LOCATION.latitude, firstStop.longitude ?? OFFICE_LOCATION.longitude] : [OFFICE_LOCATION.latitude, OFFICE_LOCATION.longitude]}
                            markers={buildDriverStopMarkers(orderedStops, route.route_type, route.route_geometry)}
                            fitToMarkers
                            showRoute
                            routeGeometry={route.route_geometry}
                            height="380px"
                            lazy
                          />
                          <MapLegend showMine={false} routeType={route.route_type === 'dropoff' ? 'dropoff' : 'pickup'} />
                          <div className="grid grid-cols-3 gap-2 mt-3">
                            <div className="rounded-lg bg-muted border border-border p-3 text-center">
                              <p className="text-xs text-muted-foreground">Distance</p>
                              <p className="text-base font-bold text-foreground mt-0.5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
                                {route.total_distance_km != null ? `${route.total_distance_km} km` : '—'}
                              </p>
                            </div>
                            <div className="rounded-lg bg-muted border border-border p-3 text-center">
                              <p className="text-xs text-muted-foreground">Duration</p>
                              <p className="text-base font-bold text-foreground mt-0.5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
                                {route.total_travel_time_min != null ? `${route.total_travel_time_min} min` : '—'}
                              </p>
                            </div>
                            <div className="rounded-lg bg-muted border border-border p-3 text-center">
                              <p className="text-xs text-muted-foreground">Stops</p>
                              <p className="text-base font-bold text-foreground mt-0.5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>{route.stops.length}</p>
                            </div>
                          </div>
                        </div>

                        {/* Stops */}
                        <div className="lg:col-span-2">
                          <p className="text-xs text-muted-foreground mb-3 uppercase tracking-wider">Stops & Passengers</p>
                          <div className="space-y-2 max-h-[500px] overflow-y-auto pr-1">
                            {orderedStops.map((stop, idx) => (
                              <div key={stop.stop_id ?? idx}>
                                <div className="rounded-xl bg-muted border border-border p-4">
                                  <div className="flex items-start gap-3">
                                    <div className={`w-7 h-7 rounded-full flex-shrink-0 flex items-center justify-center text-xs font-bold text-white ${
                                      isPickup ? 'bg-sky-500' : 'bg-emerald-500'
                                    }`}>
                                      {stop.sequence_order}
                                    </div>
                                    <div className="flex-1 min-w-0">
                                      <div className="flex items-center justify-between mb-1 gap-3">
                                        <div className="min-w-0">
                                          {/* The solver names its own stops ("Agargaon Metro
                                              Station", "Mazar Road Bus Stop"); that beats a
                                              reverse-geocoded street, and skips the lookup.
                                              An *ad-hoc* stop's "name" is a synthetic key instead
                                              ("Ad-hoc (Employee Name)" / "Home (Employee Name)") —
                                              the passenger's name is already shown below, so here
                                              the driver needs the actual door address instead. */}
                                          {stop.stop_name && !isSyntheticStopName(stop.stop_name, stop.is_adhoc) ? (
                                            <p className="text-sm font-medium text-foreground truncate">
                                              {stop.stop_name}
                                            </p>
                                          ) : (
                                            <AddressText
                                              lat={stop.latitude}
                                              lng={stop.longitude}
                                              className="text-sm font-medium text-foreground truncate"
                                            />
                                          )}
                                          {stop.latitude != null && stop.longitude != null && (
                                            <p className="text-xs text-muted-foreground font-mono mt-0.5">
                                              {stop.latitude.toFixed(5)}, {stop.longitude.toFixed(5)}
                                            </p>
                                          )}
                                        </div>
                                        <div className="flex items-center gap-2 flex-shrink-0">
                                          {stop.arrival_time && (
                                            <span className="text-xs text-muted-foreground font-mono">{stop.arrival_time}</span>
                                          )}
                                          <span className={`text-xs px-1.5 py-0.5 rounded font-medium border ${isPickup ? 'bg-sky-50 dark:bg-sky-400/20 text-sky-700 dark:text-sky-300 border-sky-200 dark:border-sky-400/30' : 'bg-emerald-50 dark:bg-emerald-400/20 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-400/30'}`}>
                                            {isPickup ? 'pickup' : 'dropoff'}
                                          </span>
                                        </div>
                                      </div>

                                      {stop.passengers.length > 0 && (
                                        <div className="space-y-1.5 mt-2">
                                          {stop.passengers.map((pax, pIdx) => (
                                            <div key={pax.employee_id ?? pIdx} className="flex items-center justify-between gap-3 bg-muted rounded-lg px-3 py-2 border border-border">
                                              <div className="flex items-center gap-2 min-w-0">
                                                <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${pax.boarded ? 'bg-emerald-500' : 'bg-amber-500'}`} />
                                                <span className="text-xs text-foreground font-medium truncate">{pax.employee_name ?? `Employee #${pax.employee_id ?? '—'}`}</span>
                                              </div>
                                              <div className="flex items-center gap-2 flex-shrink-0">
                                                <span className="text-xs text-muted-foreground whitespace-nowrap">{pax.boarded ? 'Boarded' : 'Waiting'}</span>
                                                <Switch
                                                  checked={!!pax.boarded}
                                                  onCheckedChange={() => void boardPassenger(route, stop.stop_id ?? 0, pax)}
                                                />
                                              </div>
                                            </div>
                                          ))}
                                        </div>
                                      )}
                                      {stop.passengers.length === 0 && (
                                        <p className="text-xs text-muted-foreground mt-1 italic">Destination stop — no boardings</p>
                                      )}
                                    </div>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </Sidebar>
  );
};
