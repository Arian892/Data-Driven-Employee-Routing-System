import React, { useEffect, useState } from 'react';
import { Sidebar } from '../shared/Sidebar';
import { User as UserIcon, Mail, Phone, MapPin, Lock, Save, CheckCircle, Car, Hash, Shield, Fuel } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { driverApi } from '../../services/transportApi';
import type { DriverSelfProfile } from '../../types/api';

export const DriverProfile: React.FC = () => {
  const { user, updateUser } = useAuth();
  const [driver, setDriver] = useState<DriverSelfProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [editMode, setEditMode] = useState(false);
  const [phone, setPhone] = useState('');
  const [licenseNo, setLicenseNo] = useState('');
  const [saved, setSaved] = useState(false);

  const [pwdForm, setPwdForm] = useState({ current: '', next: '', confirm: '' });
  const [pwdError, setPwdError] = useState('');
  const [pwdSaved, setPwdSaved] = useState(false);

  useEffect(() => {
    const loadProfile = async () => {
      try {
        setLoading(true);
        const data = await driverApi.getMe();
        setDriver(data);
        setPhone(data.phone ?? '');
        setLicenseNo(data.license_no ?? '');
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Unable to load profile');
      } finally {
        setLoading(false);
      }
    };

    void loadProfile();
  }, []);

  const handleSave = async () => {
    try {
      const updated = await driverApi.updateMe({ phone, license_no: licenseNo });
      setDriver(updated);
      updateUser({ phone: updated.phone ?? '' });
      setSaved(true);
      setEditMode(false);
      setTimeout(() => setSaved(false), 3000);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Unable to update profile');
    }
  };

  const handleChangePwd = async (e: React.FormEvent) => {
    e.preventDefault();
    setPwdError('');
    if (pwdForm.next !== pwdForm.confirm) { setPwdError('Passwords do not match.'); return; }
    if (pwdForm.next.length < 6) { setPwdError('Min 6 characters.'); return; }
    await new Promise(r => setTimeout(r, 600));
    setPwdSaved(true);
    setPwdForm({ current: '', next: '', confirm: '' });
    setTimeout(() => setPwdSaved(false), 3000);
  };

  return (
    <Sidebar role="driver">
      <div className="p-6 max-w-[1600px] mx-auto">
        <div className="mb-8">
          <h1 className="text-3xl font-bold text-foreground mb-1" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>My Profile</h1>
          <p className="text-muted-foreground text-sm">Your driver information and vehicle assignment.</p>
        </div>

        {/* Avatar header */}
        <div className="rounded-xl border border-border bg-card p-6 mb-5 flex items-center gap-5">
          <div className="w-16 h-16 rounded-2xl flex items-center justify-center text-2xl font-bold text-[#0B0F1A] flex-shrink-0" style={{ background: '#14B8A6' }}>
            {user?.name?.split(' ').map(n => n[0]).join('').slice(0, 2)}
          </div>
          <div className="flex-1">
            <h2 className="text-xl font-bold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>{driver?.name ?? user?.name}</h2>
            <p className="text-muted-foreground text-sm">{driver?.email ?? user?.email}</p>
            <div className="flex items-center gap-3 mt-2">
              <span className="text-xs px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-400/20 border border-emerald-200 dark:border-emerald-400/30 text-emerald-700 dark:text-emerald-300">
                Driver
              </span>
              {driver?.status && (
                <span className="text-xs px-2 py-0.5 rounded bg-muted border border-border text-muted-foreground">
                  {driver.status}
                </span>
              )}
            </div>
          </div>
          <button
            onClick={() => setEditMode(!editMode)}
            className={`px-4 py-2.5 rounded-lg text-sm font-medium border transition ${editMode ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-muted text-foreground hover:bg-border'}`}
          >
            {editMode ? 'Cancel' : 'Edit'}
          </button>
        </div>

        {error && <p className="text-sm text-red-600 dark:text-red-400 mb-4">{error}</p>}
        {loading && <p className="text-sm text-muted-foreground mb-4">Loading profile…</p>}

        {/* Vehicle card */}
        {driver?.vehicle && (
          <div className="rounded-xl border border-border bg-card p-5 mb-5">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-9 h-9 rounded-lg bg-muted border border-border flex items-center justify-center">
                <Car className="w-5 h-5 text-foreground" />
              </div>
              <h3 className="text-sm font-semibold text-foreground" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>Assigned Vehicle</h3>
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
              {[
                { label: 'Plate Number', value: driver.vehicle.plate_no, icon: Hash },
                { label: 'Model', value: driver.vehicle.model, icon: Car },
                { label: 'Type', value: driver.vehicle.make, icon: Fuel },
                { label: 'Capacity', value: 'Assigned vehicle', icon: UserIcon },
                { label: 'Status', value: driver.status, icon: Shield },
              ].map(({ label, value, icon: Icon }) => (
                <div key={label} className="flex items-start gap-2">
                  <Icon className="w-4 h-4 text-muted-foreground mt-0.5" />
                  <div>
                    <p className="text-xs text-muted-foreground uppercase tracking-wider">{label}</p>
                    <p className="text-sm text-foreground font-medium">{value}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Personal info */}
        <div className="rounded-xl border border-border bg-card p-6 mb-5">
          <h3 className="text-sm font-semibold text-foreground mb-5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>Personal Information</h3>
          {editMode ? (
            <div className="space-y-4 max-w-2xl">
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">Phone Number</label>
                  <div className="relative">
                    <Phone className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      type="text"
                      value={phone}
                      onChange={e => setPhone(e.target.value)}
                      className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6] transition"
                    />
                  </div>
                </div>
                <div>
                  <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">License Number</label>
                  <div className="relative">
                    <Shield className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      type="text"
                      value={licenseNo}
                      onChange={e => setLicenseNo(e.target.value)}
                      className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6] transition"
                    />
                  </div>
                </div>
              </div>
              <button onClick={handleSave} className="flex items-center gap-2 px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 font-semibold text-sm transition">
                <Save className="w-4 h-4" /> Save Changes
              </button>
              {saved && <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1"><CheckCircle className="w-3 h-3" /> Saved.</p>}
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8">
              {[
                { label: 'Full Name', value: driver?.name ?? user?.name, icon: UserIcon },
                { label: 'Email', value: driver?.email ?? user?.email, icon: Mail },
                { label: 'Phone', value: driver?.phone ?? user?.phone, icon: Phone },
                { label: 'License Number', value: driver?.license_no, icon: Shield },
              ].map(({ label, value, icon: Icon }) => (
                <div key={label} className="flex items-start gap-3 py-3 border-b border-border">
                  <Icon className="w-4 h-4 text-muted-foreground mt-0.5" />
                  <div>
                    <p className="text-xs text-muted-foreground uppercase tracking-wider mb-0.5">{label}</p>
                    <p className="text-sm text-foreground">{value || '—'}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Password */}
        <div className="rounded-xl border border-border bg-card p-6">
          <h3 className="text-sm font-semibold text-foreground mb-5" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>Change Password</h3>
          <form onSubmit={handleChangePwd} className="space-y-4 max-w-md">
            {[
              { label: 'Current Password', field: 'current' },
              { label: 'New Password', field: 'next' },
              { label: 'Confirm New Password', field: 'confirm' },
            ].map(({ label, field }) => (
              <div key={field}>
                <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">{label}</label>
                <div className="relative">
                  <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <input
                    type="password"
                    value={(pwdForm as any)[field]}
                    onChange={e => setPwdForm(prev => ({ ...prev, [field]: e.target.value }))}
                    className="w-full pl-10 pr-4 py-3 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6] transition"
                  />
                </div>
              </div>
            ))}
            {pwdError && <p className="text-xs text-red-600 dark:text-red-400">{pwdError}</p>}
            {pwdSaved && <p className="text-xs text-emerald-600 dark:text-emerald-400 flex items-center gap-1"><CheckCircle className="w-3 h-3" /> Password updated.</p>}
            <button type="submit" className="flex items-center gap-2 px-5 py-2.5 rounded-lg border border-[#14B8A6] bg-muted hover:bg-border text-[#14B8A6] font-semibold text-sm transition">
              <Lock className="w-4 h-4" /> Update Password
            </button>
          </form>
        </div>
      </div>
    </Sidebar>
  );
};
