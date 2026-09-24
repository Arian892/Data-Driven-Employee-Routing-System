import React, { useEffect, useState } from 'react';
import { Sidebar } from '../shared/Sidebar';
import {
  User as UserIcon,
  Mail,
  Phone,
  MapPin,
  Lock,
  Save,
  CheckCircle,
  Edit3,
  Loader2,
  Navigation,
  AlertCircle,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { dropoffRequestApi, employeeApi, pickupRequestApi } from '../../services/transportApi';
import type { Employee, ScheduleLeg, ScheduleResponse } from '../../types/api';
import { MapContainer, TileLayer, Marker, useMapEvents } from 'react-leaflet';
import L from 'leaflet';
import { InteractiveMap } from '../shared/InteractiveMap';
import { AddressText } from '../shared/AddressText';
import { ScheduleLegDetails, buildLegMarkers, coordinateLabel } from '../shared/ScheduleLeg';

// Fix Leaflet default marker icons for Vite (uses CDN to avoid asset-pipeline issues)
// eslint-disable-next-line @typescript-eslint/no-explicit-any
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
  iconRetinaUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon-2x.png',
  iconUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-icon.png',
  shadowUrl: 'https://unpkg.com/leaflet@1.9.4/dist/images/marker-shadow.png',
});

const DEFAULT_CENTER: [number, number] = [23.8103, 90.4125]; // Dhaka

// ── Map sub-components ──────────────────────────────────────────────────────

const ClickHandler: React.FC<{ onPick: (pos: [number, number]) => void }> = ({ onPick }) => {
  useMapEvents({ click: (e) => onPick([e.latlng.lat, e.latlng.lng]) });
  return null;
};

const LocationPicker: React.FC<{
  position: [number, number] | null;
  onChange: (pos: [number, number]) => void;
}> = ({ position, onChange }) => (
  <div className="rounded-lg overflow-hidden border border-border" style={{ height: 240 }}>
    <MapContainer
      center={position ?? DEFAULT_CENTER}
      zoom={position ? 14 : 11}
      style={{ height: '100%', width: '100%' }}
    >
      <TileLayer
        url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
        attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
      />
      <ClickHandler onPick={onChange} />
      {position && (
        <Marker
          position={position}
          draggable
          eventHandlers={{
            dragend(e) {
              const ll = e.target.getLatLng();
              onChange([ll.lat, ll.lng]);
            },
          }}
        />
      )}
    </MapContainer>
  </div>
);

// ── Main component ──────────────────────────────────────────────────────────

export const EmployeeProfile: React.FC = () => {
  const { user, updateUser } = useAuth();

  // Profile state
  const [profile, setProfile] = useState<Employee | null>(null);
  const [profileLoading, setProfileLoading] = useState(true);
  const [profileError, setProfileError] = useState<string | null>(null);

  // Edit state
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedOk, setSavedOk] = useState(false);
  const [form, setForm] = useState({ name: '', phone: '' });
  const [homePos, setHomePos] = useState<[number, number] | null>(null);

  // Schedule state
  const [schedule, setSchedule] = useState<ScheduleResponse | null>(null);
  const [schedLoading, setSchedLoading] = useState(true);
  // Distinguishes "you have no pickup/dropoff request for today at all" from
  // "you have one, but the admin hasn't run routing yet" — `getSchedule()`
  // alone can't tell these apart (both come back as the same empty shape), so
  // this checks the raw request tables directly.
  const [hasRequestToday, setHasRequestToday] = useState<boolean | null>(null);

  // Password change state
  const [pwdForm, setPwdForm] = useState({ current: '', next: '', confirm: '' });
  const [pwdError, setPwdError] = useState('');
  const [pwdSaved, setPwdSaved] = useState(false);

  // Load profile on mount
  useEffect(() => {
    setProfileLoading(true);
    employeeApi
      .getProfile()
      .then((data) => {
        setProfile(data);
        setForm({ name: data.name, phone: data.phone ?? '' });
        if (data.home_lat != null && data.home_lng != null) {
          setHomePos([data.home_lat, data.home_lng]);
        }
      })
      .catch(() => setProfileError('Could not load profile. Is the backend running?'))
      .finally(() => setProfileLoading(false));
  }, []);

  // Load today's schedule on mount
  useEffect(() => {
    setSchedLoading(true);
    employeeApi
      .getSchedule()
      .then(setSchedule)
      .catch(() => setSchedule({ routing_done: false }))
      .finally(() => setSchedLoading(false));
  }, []);

  // Check whether today has a pickup/dropoff request at all, so the "no
  // route yet" panel can tell "nothing requested today" apart from "requested,
  // but routing hasn't run" instead of always blaming the admin.
  useEffect(() => {
    const today = new Date().toISOString().split('T')[0];
    Promise.all([
      pickupRequestApi.mine({ service_date: today, limit: 1 }),
      dropoffRequestApi.mine({ service_date: today, limit: 1 }),
    ])
      .then(([pickups, dropoffs]) => {
        setHasRequestToday(pickups.pickup_requests.length > 0 || dropoffs.dropoff_requests.length > 0);
      })
      .catch(() => setHasRequestToday(null));
  }, []);

  // ── Handlers ──────────────────────────────────────────────────────────────

  const startEdit = () => {
    if (!profile) return;
    setForm({ name: profile.name, phone: profile.phone ?? '' });
    setHomePos(
      profile.home_lat != null && profile.home_lng != null
        ? [profile.home_lat, profile.home_lng]
        : null,
    );
    setSavedOk(false);
    setEditMode(true);
  };

  const cancelEdit = () => setEditMode(false);

  const handleSaveProfile = async () => {
    setSaving(true);
    try {
      const updated = await employeeApi.updateProfile({
        name: form.name.trim() || undefined,
        phone: form.phone.trim() || undefined,
        home_lat: homePos?.[0],
        home_lng: homePos?.[1],
      });
      setProfile(updated);
      updateUser({ name: updated.name, phone: updated.phone ?? undefined });
      setEditMode(false);
      setSavedOk(true);
      setTimeout(() => setSavedOk(false), 3000);
    } catch {
      setProfileError('Failed to save. Please try again.');
    } finally {
      setSaving(false);
    }
  };

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setPwdError('');
    if (pwdForm.next !== pwdForm.confirm) {
      setPwdError('New passwords do not match.');
      return;
    }
    if (pwdForm.next.length < 6) {
      setPwdError('Password must be at least 6 characters.');
      return;
    }
    try {
      await employeeApi.changePassword({
        current_password: pwdForm.current,
        new_password: pwdForm.next,
      });
      setPwdSaved(true);
      setPwdForm({ current: '', next: '', confirm: '' });
      setTimeout(() => setPwdSaved(false), 3000);
    } catch (err) {
      setPwdError(err instanceof Error ? err.message : 'Could not change password.');
    }
  };

  // ── Loading / error states ─────────────────────────────────────────────────

  if (profileLoading) {
    return (
      <Sidebar role="employee">
        <div className="flex items-center justify-center h-64">
          <Loader2 className="w-8 h-8 animate-spin text-sky-600 dark:text-sky-400" />
        </div>
      </Sidebar>
    );
  }

  if (profileError && !profile) {
    return (
      <Sidebar role="employee">
        <div className="p-6 max-w-[1600px] mx-auto">
          <div className="rounded-xl border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/10 p-4 text-red-600 dark:text-red-400 text-sm flex items-center gap-2">
            <AlertCircle className="w-4 h-4 flex-shrink-0" />
            {profileError}
          </div>
        </div>
      </Sidebar>
    );
  }

  const p = profile!;
  const initials = p.name.split(' ').map((n) => n[0]).join('').slice(0, 2).toUpperCase();

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <Sidebar role="employee">
      <div className="p-6 max-w-[1600px] mx-auto space-y-5">

        {/* Page title */}
        <div className="mb-2">
          <h1 className="text-3xl font-bold text-foreground mb-1" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
            My Profile
          </h1>
          <p className="text-muted-foreground text-sm">Manage your personal information and account settings.</p>
        </div>

        {profileError && (
          <div className="rounded-lg border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/10 px-4 py-2 text-red-600 dark:text-red-400 text-sm">
            {profileError}
          </div>
        )}

        {/* ── Avatar / header card ─────────────────────────────────────────── */}
        <div className="rounded-xl border border-border bg-card p-6 flex items-center gap-5">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-2xl font-bold text-[#0B0F1A] flex-shrink-0" style={{ background: '#14B8A6' }}>
            {initials}
          </div>
          <div className="flex-1">
            <h2 className="text-xl font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              {p.name}
            </h2>
            <p className="text-muted-foreground text-sm">{p.email}</p>
            <div className="flex items-center gap-2 mt-2">
              <span className="text-xs px-2 py-0.5 rounded bg-muted border border-border text-foreground capitalize">
                {p.role}
              </span>
              <span
                className={`text-xs px-2 py-0.5 rounded border font-medium ${
                  p.status === 'Active'
                    ? 'bg-emerald-50 dark:bg-emerald-400/20 border-emerald-200 dark:border-emerald-400/30 text-emerald-700 dark:text-emerald-300'
                    : 'bg-muted border-border text-muted-foreground'
                }`}
              >
                {p.status}
              </span>
            </div>
          </div>
          <button
            onClick={editMode ? cancelEdit : startEdit}
            className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-medium transition border ${
              editMode
                ? 'border-border bg-muted text-foreground'
                : 'border-border bg-muted text-muted-foreground hover:text-foreground'
            }`}
          >
            <Edit3 className="w-4 h-4" />
            {editMode ? 'Cancel' : 'Edit'}
          </button>
        </div>

        {/* Success banner */}
        {savedOk && (
          <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-emerald-50 dark:bg-emerald-500/10 border border-emerald-200 dark:border-emerald-500/20 text-emerald-600 dark:text-emerald-400 text-sm">
            <CheckCircle className="w-4 h-4" />
            Profile saved successfully.
          </div>
        )}

        {/* ── Profile info card ────────────────────────────────────────────── */}
        <div className="rounded-xl border border-border bg-card p-6">
          <h3 className="text-sm font-semibold text-foreground mb-5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
            Personal Information
          </h3>

          {editMode ? (
            /* Edit mode */
            <div className="space-y-4">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                {/* Name */}
                <div>
                  <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">Full Name</label>
                  <div className="relative">
                    <UserIcon className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      type="text"
                      value={form.name}
                      onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                      placeholder="Your full name"
                      className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                    />
                  </div>
                </div>

                {/* Phone */}
                <div>
                  <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">Phone Number</label>
                  <div className="relative">
                    <Phone className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      type="text"
                      value={form.phone}
                      onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
                      placeholder="+880-17xx-xxxxxx"
                      className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                    />
                  </div>
                </div>
              </div>

              {/* Home location */}
              <div>
                <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">
                  Home Location
                </label>
                {homePos && (
                  <div className="mb-2">
                    <p className="text-sm">
                      <AddressText lat={homePos[0]} lng={homePos[1]} className="text-foreground" />
                    </p>
                    <p className="text-xs text-muted-foreground font-mono">
                      {coordinateLabel(homePos[0], homePos[1])}
                    </p>
                  </div>
                )}
                <LocationPicker position={homePos} onChange={setHomePos} />
                <p className="text-xs text-muted-foreground mt-1.5">
                  Click the map or drag the marker to update your home location.
                </p>
                {/* Manual coordinate inputs */}
                <div className="grid grid-cols-2 gap-3 mt-3">
                  <div>
                    <label className="block text-xs text-muted-foreground mb-1">Latitude</label>
                    <input
                      type="number"
                      step="any"
                      value={homePos?.[0] ?? ''}
                      onChange={(e) => {
                        const v = parseFloat(e.target.value);
                        if (!isNaN(v)) setHomePos([v, homePos?.[1] ?? DEFAULT_CENTER[1]]);
                      }}
                      placeholder="23.8103"
                      className="w-full px-3 py-2 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-xs focus:outline-none focus:border-[#14B8A6]/50 transition font-mono"
                    />
                  </div>
                  <div>
                    <label className="block text-xs text-muted-foreground mb-1">Longitude</label>
                    <input
                      type="number"
                      step="any"
                      value={homePos?.[1] ?? ''}
                      onChange={(e) => {
                        const v = parseFloat(e.target.value);
                        if (!isNaN(v)) setHomePos([homePos?.[0] ?? DEFAULT_CENTER[0], v]);
                      }}
                      placeholder="90.4125"
                      className="w-full px-3 py-2 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-xs focus:outline-none focus:border-[#14B8A6]/50 transition font-mono"
                    />
                  </div>
                </div>
              </div>

              {/* Save button */}
              <button
                onClick={handleSaveProfile}
                disabled={saving}
                className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-primary hover:opacity-90 disabled:opacity-60 text-primary-foreground font-semibold text-sm transition"
              >
                {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {saving ? 'Saving…' : 'Save Changes'}
              </button>
            </div>
          ) : (
            /* View mode */
            <div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8">
                {[
                  { label: 'Full Name', value: p.name, icon: UserIcon },
                  { label: 'Email Address', value: p.email, icon: Mail },
                  { label: 'Phone Number', value: p.phone, icon: Phone },
                ].map(({ label, value, icon: Icon }) => (
                  <div key={label} className="flex items-start gap-3 py-3 border-b border-border">
                    <Icon className="w-4 h-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                    <div>
                      <p className="text-xs text-muted-foreground uppercase tracking-wider mb-0.5">{label}</p>
                      <p className="text-sm text-foreground font-mono">{value || '—'}</p>
                    </div>
                  </div>
                ))}

                <div className="flex items-start gap-3 py-3 border-b border-border">
                  <MapPin className="w-4 h-4 text-muted-foreground mt-0.5 flex-shrink-0" />
                  <div>
                    <p className="text-xs text-muted-foreground uppercase tracking-wider mb-0.5">Home Location</p>
                    <p className="text-sm">
                      <AddressText lat={p.home_lat} lng={p.home_lng} className="text-foreground" />
                    </p>
                    {p.home_lat != null && p.home_lng != null && (
                      <p className="text-xs text-muted-foreground font-mono mt-0.5">
                        {coordinateLabel(p.home_lat, p.home_lng)}
                      </p>
                    )}
                  </div>
                </div>
              </div>

              {/* Home location map — the same fully-interactive InteractiveMap
                  used everywhere else in the app (pan/zoom/scroll all work),
                  not a frozen static preview. */}
              {p.home_lat != null && p.home_lng != null && (
                <div className="pt-4">
                  <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">Home on Map</p>
                  <InteractiveMap
                    center={[p.home_lat, p.home_lng]}
                    zoom={14}
                    markers={[{ position: [p.home_lat, p.home_lng], label: 'Your Home', variant: 'mine', code: 'H' }]}
                    height="440px"
                    lazy
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {/* ── Today's Schedule card ────────────────────────────────────────── */}
        <div className="rounded-xl border border-border bg-card p-6">
          <h3
            className="text-sm font-semibold text-foreground mb-5 flex items-center gap-2"
            style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}
          >
            <Navigation className="w-4 h-4 text-sky-600 dark:text-sky-400" />
            Today's Assigned Route
          </h3>

          {schedLoading ? (
            <div className="flex items-center justify-center py-8">
              <Loader2 className="w-6 h-6 animate-spin text-sky-600 dark:text-sky-400" />
            </div>
          ) : !schedule?.routing_done ? (
            <div className="flex flex-col items-center justify-center py-8 text-center">
              <MapPin className="w-8 h-8 text-muted-foreground mb-3" />
              {hasRequestToday === false ? (
                <>
                  <p className="text-foreground text-sm font-medium">No trip scheduled for today</p>
                  <p className="text-muted-foreground text-xs mt-1 max-w-xs">
                    You don't have a pickup or dropoff request for today's shift.
                  </p>
                </>
              ) : (
                <>
                  <p className="text-foreground text-sm font-medium">No route assigned yet</p>
                  <p className="text-muted-foreground text-xs mt-1 max-w-xs">
                    The admin hasn't run routing for today. Check back once routing is complete.
                  </p>
                </>
              )}
            </div>
          ) : (
            <div className="space-y-5">
              {/* Both halves of the night, in travel order — same layout as My
                  Requests, since pickup and dropoff are separate vehicles/routes. */}
              {(() => {
                const legs = [schedule.pickup, schedule.dropoff].filter((l): l is ScheduleLeg => Boolean(l));
                return (
                  <>
                    {legs.map(leg => (
                      <div key={leg.route_id} className="grid grid-cols-1 md:grid-cols-5 gap-6">
                        <div className="md:col-span-2 rounded-lg border border-border bg-muted px-4 py-3">
                          <p className="text-xs text-muted-foreground uppercase tracking-wider mb-2">
                            {leg.route_type === 'pickup' ? 'Pickup Route' : 'Dropoff Route'}
                          </p>
                          <ScheduleLegDetails leg={leg} />
                        </div>

                        <div className="md:col-span-3 rounded-lg overflow-hidden border border-border" style={{ height: 440 }}>
                          <InteractiveMap
                            center={[leg.stop.latitude, leg.stop.longitude]}
                            zoom={14}
                            markers={buildLegMarkers(leg)}
                            fitToMarkers
                            showRoute
                            routeGeometry={leg.route_geometry}
                            height="440px"
                          />
                        </div>
                      </div>
                    ))}
                    {legs.length === 1 && (
                      <p className="text-xs text-muted-foreground">
                        {legs[0].route_type === 'pickup'
                          ? 'Your ride home has not been assigned for today.'
                          : 'Your ride to the office has not been assigned for today.'}
                      </p>
                    )}
                  </>
                );
              })()}
            </div>
          )}
        </div>

        {/* ── Password change card ─────────────────────────────────────────── */}
        <div className="rounded-xl border border-border bg-card p-6">
          <h3 className="text-sm font-semibold text-foreground mb-5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
            Change Password
          </h3>
          <form onSubmit={handleChangePassword} className="space-y-4 max-w-md">
            {[
              { label: 'Current Password', field: 'current', placeholder: 'Enter current password' },
              { label: 'New Password', field: 'next', placeholder: 'At least 6 characters' },
              { label: 'Confirm New Password', field: 'confirm', placeholder: 'Repeat new password' },
            ].map(({ label, field, placeholder }) => (
              <div key={field}>
                <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">{label}</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <input
                    type="password"
                    // eslint-disable-next-line @typescript-eslint/no-explicit-any
                    value={(pwdForm as any)[field]}
                    onChange={(e) =>
                      setPwdForm((prev) => ({ ...prev, [field]: e.target.value }))
                    }
                    placeholder={placeholder}
                    className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                  />
                </div>
              </div>
            ))}
            {pwdError && <p className="text-xs text-red-600 dark:text-red-400">{pwdError}</p>}
            {pwdSaved && (
              <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1">
                <CheckCircle className="w-3 h-3" /> Password changed successfully.
              </p>
            )}
            <button
              type="submit"
              className="flex items-center gap-2 px-5 py-2.5 rounded-lg border border-[#14B8A6] bg-muted hover:bg-accent/10 text-[#14B8A6] font-semibold text-sm transition"
            >
              <Lock className="w-4 h-4" />
              Update Password
            </button>
          </form>
        </div>

      </div>
    </Sidebar>
  );
};
