import React, { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import {
  Users, Car, Truck, ClipboardList, Route, LogOut,
  Plus, Trash2, Key, Eye, Search, Filter, ChevronDown,
  Bus, MapPin, Clock, AlertCircle,
  X, Edit, Phone, Mail, Hash, UserCog,
  BarChart3, Shield, CheckCircle, Sun, Moon,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import { adminApi, driverApi, dropoffRequestApi, employeeApi, pickupRequestApi, vehicleApi } from '../../services/transportApi';
import type { Driver, DropoffRequest, Employee, PickupRequest, Vehicle, PickupRoutingInputResponse, ScheduleSummaryResponse, RouteDetailResponse } from '../../types/api';
import { InteractiveMap } from '../shared/InteractiveMap';
import { buildDriverStopMarkers, MapLegend } from '../shared/ScheduleLeg';
import { RouteMapBackdrop } from '../shared/RouteMapBackdrop';
import { OFFICE_LOCATION } from '../../data/mockData';

type AdminView = 'overview' | 'employees' | 'drivers' | 'vehicles' | 'requests' | 'routing';

interface AdminEmployee {
  id: string;
  name: string;
  email: string;
  phone: string;
  role: string;
  latitude?: number | null;
  longitude?: number | null;
  employeeId: string;
  status?: string;
}

const SIDEBAR_ITEMS = [
  { id: 'overview' as AdminView, label: 'Overview', icon: BarChart3 },
  { id: 'employees' as AdminView, label: 'Employees', icon: Users },
  { id: 'drivers' as AdminView, label: 'Drivers', icon: Truck },
  { id: 'vehicles' as AdminView, label: 'Vehicles', icon: Car },
  { id: 'requests' as AdminView, label: 'Requests', icon: ClipboardList },
  { id: 'routing' as AdminView, label: 'Routing', icon: Route },
];

/** One pickup or dropoff request, shaped the same way regardless of which
 * table it came from — lets the Overview widget and Requests tab share one
 * rendering path instead of duplicating it per request type. */
type UnifiedRequest = {
  kind: 'pickup' | 'dropoff';
  id: number;
  employeeName?: string | null;
  employeeId?: number | null;
  serviceDate: string;
  shiftTime?: string | null;
  status: string;
  lat?: number | null;
  lng?: number | null;
  zoneName?: string | null;
  createdAt?: string | null;
};

const toUnifiedPickup = (r: PickupRequest): UnifiedRequest => ({
  kind: 'pickup', id: r.pickup_id, employeeName: r.employee_name, employeeId: r.employee_id,
  serviceDate: r.service_date, shiftTime: r.shift_start_time, status: r.status,
  lat: r.pickup_lat, lng: r.pickup_lng, zoneName: r.zone_name, createdAt: r.created_at,
});

const toUnifiedDropoff = (r: DropoffRequest): UnifiedRequest => ({
  kind: 'dropoff', id: r.dropoff_id, employeeName: r.employee_name, employeeId: r.employee_id,
  serviceDate: r.service_date, shiftTime: r.shift_end_time, status: r.status,
  lat: r.drop_lat, lng: r.drop_lng, zoneName: r.zone_name, createdAt: r.created_at,
});

export const AdminDashboard: React.FC = () => {
  const { user, logout } = useAuth();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const [view, setView] = useState<AdminView>('overview');
  const [employees, setEmployees] = useState<AdminEmployee[]>([]);
  const [searchQ, setSearchQ] = useState('');

  // Modals
  const [addEmpOpen, setAddEmpOpen] = useState(false);
  const [resetPwdUser, setResetPwdUser] = useState<AdminEmployee | null>(null);
  const [resetPwdNew, setResetPwdNew] = useState('');
  const [resetPwdError, setResetPwdError] = useState('');
  const [resetSaving, setResetSaving] = useState(false);
  const [deleteUser, setDeleteUser] = useState<AdminEmployee | null>(null);
  const [actionSaving, setActionSaving] = useState(false);
  const [viewEmpDetail, setViewEmpDetail] = useState<AdminEmployee | null>(null);
  const [routingResult, setRoutingResult] = useState<ScheduleSummaryResponse | null>(null);
  // Local calendar date, not `toISOString()` — that converts to UTC first,
  // which silently shows "yesterday" for any admin east of UTC (e.g. Dhaka,
  // UTC+6) during the first hours of the local day.
  const now = new Date();
  const todayIso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const [selectedDateFilter, setSelectedDateFilter] = useState(todayIso);
  const [routingDate, setRoutingDate] = useState(todayIso);
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [vehicles, setVehicles] = useState<Vehicle[]>([]);
  // Scoped to `selectedDateFilter` — see `loadRequestsForDate`. Never the
  // full table: with thousands of historical rows, only the Overview KPIs
  // and "recent" widget need a global view, and those come from their own
  // lightweight, count-only fetches instead.
  const [pickupRequests, setPickupRequests] = useState<PickupRequest[]>([]);
  const [dropoffRequests, setDropoffRequests] = useState<DropoffRequest[]>([]);
  const [recentRequests, setRecentRequests] = useState<UnifiedRequest[]>([]);
  const [pendingRequests, setPendingRequests] = useState(0);
  const [routedRequests, setRoutedRequests] = useState(0);
  const [requestsLoading, setRequestsLoading] = useState(false);
  const [pickupPreview, setPickupPreview] = useState<PickupRoutingInputResponse | null>(null);
  const [dropoffPreview, setDropoffPreview] = useState<PickupRoutingInputResponse | null>(null);
  const [apiLoading, setApiLoading] = useState(false);
  const [apiError, setApiError] = useState<string | null>(null);
  const [requestActionLoading, setRequestActionLoading] = useState(false);
  const [isRunningDay, setIsRunningDay] = useState(false);
  const [runDayMessage, setRunDayMessage] = useState<string | null>(null);

  const loadAdminApiData = async () => {
    setApiLoading(true);
    setApiError(null);
    try {
      // Employees: loop every page. A single capped page silently hides
      // whoever sorts past the cutoff — exactly what happened with the
      // "Shift Finisher" batch, whose employee_ids start well past row 100.
      const employeesAll: Employee[] = [];
      let empPage = 1;
      while (true) {
        const res = await employeeApi.list({ page: empPage, limit: 500 });
        employeesAll.push(...res.employees);
        if (empPage >= res.pagination.total_pages || res.employees.length === 0) break;
        empPage += 1;
      }

      const [driverRes, vehicleRes, pendingPickupRes, pendingDropoffRes, approvedPickupRes, approvedDropoffRes, recentPickupRes, recentDropoffRes] = await Promise.all([
        driverApi.list({ page: 1, limit: 100 }),
        vehicleApi.list({ page: 1, limit: 100 }),
        // Lightweight KPI counts: fetch 1 row just to read the accurate
        // `pagination.total_items` the backend already computed, rather
        // than shipping every matching row to the browser to count them.
        pickupRequestApi.list({ status: 'Pending', limit: 1 }),
        dropoffRequestApi.list({ status: 'Pending', limit: 1 }),
        pickupRequestApi.list({ status: 'Approved', limit: 1 }),
        dropoffRequestApi.list({ status: 'Approved', limit: 1 }),
        // Backend already orders by created_at desc — 5 from each side is
        // plenty to find the 5 most recent overall.
        pickupRequestApi.list({ limit: 5 }),
        dropoffRequestApi.list({ limit: 5 }),
      ]);

      setEmployees(employeesAll.map(emp => ({
        id: String(emp.user_id),
        name: emp.name,
        email: emp.email,
        phone: emp.phone ?? '',
        role: 'employee',
        latitude: emp.home_lat ?? undefined,
        longitude: emp.home_lng ?? undefined,
        employeeId: String(emp.employee_id),
        status: emp.status,
      })));
      setDrivers(driverRes.drivers);
      setVehicles(vehicleRes.vehicles);
      setPendingRequests(pendingPickupRes.pagination.total_items + pendingDropoffRes.pagination.total_items);
      setRoutedRequests(approvedPickupRes.pagination.total_items + approvedDropoffRes.pagination.total_items);
      const recent = [
        ...recentPickupRes.pickup_requests.map(toUnifiedPickup),
        ...recentDropoffRes.dropoff_requests.map(toUnifiedDropoff),
      ]
        .sort((a, b) => (b.createdAt ?? '').localeCompare(a.createdAt ?? ''))
        .slice(0, 5);
      setRecentRequests(recent);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not load backend data.');
    } finally {
      setApiLoading(false);
    }
  };

  /** Every pickup + dropoff request for exactly one service date — the
   * Requests tab's own view, kept separate from the global KPIs above so
   * switching dates never re-fetches everything else on the page. Loops
   * pages since a single busy date can exceed the backend's 500-row cap. */
  const loadRequestsForDate = async (date: string) => {
    setRequestsLoading(true);
    setApiError(null);
    try {
      const limit = 500;
      const fetchAllPickups = async () => {
        const all: PickupRequest[] = [];
        let page = 1;
        while (true) {
          const res = await pickupRequestApi.list({ service_date: date, page, limit });
          all.push(...res.pickup_requests);
          if (page >= res.pagination.total_pages || res.pickup_requests.length === 0) break;
          page += 1;
        }
        return all;
      };
      const fetchAllDropoffs = async () => {
        const all: DropoffRequest[] = [];
        let page = 1;
        while (true) {
          const res = await dropoffRequestApi.list({ service_date: date, page, limit });
          all.push(...res.dropoff_requests);
          if (page >= res.pagination.total_pages || res.dropoff_requests.length === 0) break;
          page += 1;
        }
        return all;
      };
      const [pickups, dropoffs] = await Promise.all([fetchAllPickups(), fetchAllDropoffs()]);
      setPickupRequests(pickups);
      setDropoffRequests(dropoffs);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not load requests for this date.');
    } finally {
      setRequestsLoading(false);
    }
  };

  useEffect(() => {
    loadAdminApiData();
  }, []);

  useEffect(() => {
    loadRequestsForDate(selectedDateFilter);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDateFilter]);

  const handleLogout = () => {
    logout();
    navigate('/login/admin');
  };

  const totalEmployees = employees.length;
  const totalDrivers = drivers.length;
  const totalVehicles = vehicles.length;
  // pickupRequests/dropoffRequests are already scoped to selectedDateFilter
  // by loadRequestsForDate, so no client-side date filtering needed here.
  const dateRequests: UnifiedRequest[] = [
    ...pickupRequests.map(toUnifiedPickup),
    ...dropoffRequests.map(toUnifiedDropoff),
  ];

  const refreshAfterRequestAction = () => Promise.all([loadRequestsForDate(selectedDateFilter), loadAdminApiData()]);

  const approveDropoff = async (dropoffId: number) => {
    setRequestActionLoading(true);
    setApiError(null);
    try {
      await dropoffRequestApi.approve(dropoffId);
      await refreshAfterRequestAction();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not approve dropoff request.');
    } finally {
      setRequestActionLoading(false);
    }
  };

  const rejectDropoff = async (dropoffId: number) => {
    setRequestActionLoading(true);
    setApiError(null);
    try {
      await dropoffRequestApi.reject(dropoffId);
      await refreshAfterRequestAction();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not reject dropoff request.');
    } finally {
      setRequestActionLoading(false);
    }
  };

  const approvePickup = async (pickupId: number) => {
    setRequestActionLoading(true);
    setApiError(null);
    try {
      await pickupRequestApi.approve(pickupId);
      await refreshAfterRequestAction();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not approve pickup request.');
    } finally {
      setRequestActionLoading(false);
    }
  };

  const rejectPickup = async (pickupId: number) => {
    setRequestActionLoading(true);
    setApiError(null);
    try {
      await pickupRequestApi.reject(pickupId);
      await refreshAfterRequestAction();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not reject pickup request.');
    } finally {
      setRequestActionLoading(false);
    }
  };

  const loadPreview = async (type: 'pickup' | 'dropoff') => {
    setApiLoading(true);
    setApiError(null);
    try {
      if (type === 'pickup') {
        const data = await adminApi.getPickupRoutingInput(routingDate);
        setPickupPreview(data);
      } else {
        const data = await adminApi.getPickupRoutingInput(routingDate);
        setDropoffPreview(data);
      }
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not load preview.');
    } finally {
      setApiLoading(false);
    }
  };

  const loadScheduleSummary = async () => {
    try {
      const summary = await adminApi.getScheduleSummary(routingDate);
      setRoutingResult(summary);
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not load schedule summary.');
    }
  };

  /** Force re-route just the selected date — pickup + dropoff together, replacing whatever was there. */
  const runDayRouting = async () => {
    setIsRunningDay(true);
    setApiError(null);
    setRunDayMessage(null);
    try {
      const result = await adminApi.runDayRouting(routingDate);
      const routes = result.pickup.routes_created + result.dropoff.routes_created;
      const assigned = result.pickup.employees_assigned + result.dropoff.employees_assigned;
      setRunDayMessage(`Re-routed ${result.service_date}: ${routes} route(s), ${assigned} employee(s) assigned.`);
      await loadScheduleSummary();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not re-route this day.');
    } finally {
      setIsRunningDay(false);
    }
  };

  // Add employee
  const [newEmp, setNewEmp] = useState({ name: '', email: '', phone: '', password: '' });
  const [addSaving, setAddSaving] = useState(false);

  const handleAddEmployee = async () => {
    if (!newEmp.name.trim() || !newEmp.email.trim() || !newEmp.password) return;
    if (newEmp.password.length < 6) {
      setApiError('Password must be at least 6 characters.');
      return;
    }
    setAddSaving(true);
    setApiError(null);
    try {
      await employeeApi.add({
        name: newEmp.name.trim(),
        email: newEmp.email.trim(),
        phone: newEmp.phone.trim() || undefined,
        password: newEmp.password,
      });
      setNewEmp({ name: '', email: '', phone: '', password: '' });
      setAddEmpOpen(false);
      await loadAdminApiData();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not add employee.');
    } finally {
      setAddSaving(false);
    }
  };

  const handleDeleteEmployee = async () => {
    if (!deleteUser) return;
    setActionSaving(true);
    setApiError(null);
    try {
      await adminApi.deleteEmployee(Number(deleteUser.id));
      setDeleteUser(null);
      await loadAdminApiData();
    } catch (err) {
      setApiError(err instanceof Error ? err.message : 'Could not deactivate employee.');
    } finally {
      setActionSaving(false);
    }
  };

  const handleResetPassword = async () => {
    if (!resetPwdUser) return;
    if (resetPwdNew.length < 6) {
      setResetPwdError('Password must be at least 6 characters.');
      return;
    }
    setResetPwdError('');
    setResetSaving(true);
    setApiError(null);
    try {
      await adminApi.resetEmployeePassword(Number(resetPwdUser.id), resetPwdNew);
      setResetPwdNew('');
      setResetPwdUser(null);
    } catch (err) {
      setResetPwdError(err instanceof Error ? err.message : 'Could not reset password.');
    } finally {
      setResetSaving(false);
    }
  };

  const filteredEmployees = employees.filter(e =>
    e.name.toLowerCase().includes(searchQ.toLowerCase()) ||
    e.email.toLowerCase().includes(searchQ.toLowerCase())
  );

  const StatCard = ({ label, value, icon: Icon, color, sub }: any) => (
    <div className="rounded-xl border border-border bg-card p-5 flex items-center gap-4">
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${color}`}>
        <Icon className="w-6 h-6" />
      </div>
      <div>
        <p className="text-2xl font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>{value}</p>
        <p className="text-sm text-muted-foreground">{label}</p>
        {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
      </div>
    </div>
  );

  const SectionHeader = ({ icon: Icon, iconColor, title, subtitle, count }: any) => (
    <div className="flex items-center gap-4">
      <div className={`w-11 h-11 rounded-xl flex items-center justify-center flex-shrink-0 ${iconColor}`}>
        <Icon className="w-5 h-5" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>{title}</h2>
          <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-muted text-muted-foreground">{count}</span>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">{subtitle}</p>
      </div>
    </div>
  );

  /** Small dot + label status pill shared by the Employees/Drivers/Vehicles tables. */
  const StatusPill = ({ active, activeLabel, inactiveLabel }: { active: boolean; activeLabel: string; inactiveLabel: string }) => (
    <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium ${
      active
        ? 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
        : 'bg-muted text-muted-foreground'
    }`}>
      <span className={`w-1.5 h-1.5 rounded-full ${active ? 'bg-emerald-500' : 'bg-muted-foreground/50'}`} />
      {active ? activeLabel : inactiveLabel}
    </span>
  );

  return (
    <div className="flex h-screen bg-background overflow-hidden">
      {/* Sidebar */}
      <aside className="w-60 flex-shrink-0 flex flex-col border-r border-white/10" style={{ background: 'var(--sidebar)' }}>
        {/* Logo */}
        <div className="px-5 py-6 border-b border-white/10">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-white/10 border border-white/15 flex items-center justify-center">
              <Shield className="w-5 h-5 text-white" />
            </div>
            <div>
              <p className="text-sm font-bold text-white tracking-wide" style={{ fontFamily: 'Rajdhani, sans-serif' }}>
                TranspoRT
              </p>
              <p className="text-xs text-slate-300">Admin Panel</p>
            </div>
          </div>
        </div>

        {/* Admin user */}
        <div className="px-4 py-4 border-b border-white/10">
          <div className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-white/4">
            <div className="w-8 h-8 rounded-full bg-white/10 border border-white/15 flex items-center justify-center text-xs font-bold text-white">
              {user?.name[0]}
            </div>
            <div className="min-w-0">
              <p className="text-xs font-semibold text-white truncate">{user?.name}</p>
              <p className="text-xs text-slate-300">Administrator</p>
            </div>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {SIDEBAR_ITEMS.map(item => {
            const Icon = item.icon;
            const active = view === item.id;
            return (
              <button
                key={item.id}
                onClick={() => setView(item.id)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm transition-all text-left ${
                  active
                    ? 'bg-[#14B8A6]/15 text-white'
                    : 'text-slate-400 hover:text-white hover:bg-white/[0.06]'
                }`}
              >
                <Icon className={`w-4 h-4 ${active ? 'text-[#14B8A6]' : ''}`} />
                <span className="font-medium">{item.label}</span>
              </button>
            );
          })}
        </nav>

        {/* Theme toggle + Logout */}
        <div className="px-3 py-4 border-t border-white/10 space-y-1">
          <button
            onClick={toggleTheme}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-300 hover:text-white hover:bg-white/[0.08] transition"
          >
            {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
            <span className="font-medium">{theme === 'dark' ? 'Light Mode' : 'Dark Mode'}</span>
          </button>
          <button
            onClick={handleLogout}
            className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm text-slate-300 hover:text-white hover:bg-white/[0.08] transition"
          >
            <LogOut className="w-4 h-4" />
            <span className="font-medium">Sign Out</span>
          </button>
        </div>
      </aside>

      {/* Main */}
      <main className="flex-1 overflow-y-auto relative">
        <RouteMapBackdrop variant="content" />
        <div className="relative z-10">
        {/* Header */}
        <div className="sticky top-0 z-20 px-8 py-4 border-b border-border bg-background/95 backdrop-blur flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>
              {SIDEBAR_ITEMS.find(i => i.id === view)?.label}
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">Transport Route Management System</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
            System Online
          </div>
        </div>

        <div className="p-8">
          {apiError && (
            <div className="mb-4 rounded-lg border border-red-200 dark:border-red-500/20 bg-red-50 dark:bg-red-500/10 px-4 py-3 text-sm text-red-600 dark:text-red-400">
              {apiError}
            </div>
          )}
          {apiLoading && (
            <div className="mb-4 rounded-lg border border-sky-200 dark:border-sky-500/20 bg-sky-50 dark:bg-sky-500/10 px-4 py-3 text-sm text-sky-700 dark:text-sky-300">
              Loading backend data...
            </div>
          )}

          {/* ─── OVERVIEW ─── */}
          {view === 'overview' && (
            <div className="space-y-8">
              <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatCard label="Total Employees" value={totalEmployees} icon={Users} color="bg-sky-50 dark:bg-sky-500/15 text-sky-600 dark:text-sky-400" />
                <StatCard label="Active Drivers" value={totalDrivers} icon={Truck} color="bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400" />
                <StatCard label="Fleet Vehicles" value={totalVehicles} icon={Car} color="bg-[#14B8A6]/15 text-[#14B8A6]" />
                <StatCard label="Pending Requests" value={pendingRequests} icon={ClipboardList} color="bg-purple-50 dark:bg-purple-500/15 text-purple-600 dark:text-purple-400" sub={`${routedRequests} routed`} />
              </div>

              <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                {/* Recent requests */}
                <div className="rounded-xl border border-border bg-card p-6">
                  <h3 className="text-base font-semibold text-foreground mb-4" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Recent Requests</h3>
                  <div className="space-y-3">
                    {recentRequests.map(r => (
                      <div key={`${r.kind}-${r.id}`} className="flex items-center justify-between py-2 border-b border-border last:border-0">
                        <div className="flex items-center gap-3">
                          <div className={`w-2 h-2 rounded-full ${r.status === 'Approved' ? 'bg-emerald-500' : r.status === 'Pending' ? 'bg-amber-500' : 'bg-red-500'}`} />
                          <div>
                            <p className="text-sm text-foreground font-medium">{r.employeeName}</p>
                            <p className="text-xs text-muted-foreground">{r.serviceDate} · {r.shiftTime} · {r.kind}</p>
                          </div>
                        </div>
                        <span className={`text-xs px-2 py-0.5 rounded-full ${
                          r.status === 'Approved' ? 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                          : r.status === 'Pending' ? 'bg-amber-50 dark:bg-amber-500/15 text-amber-600 dark:text-amber-400'
                          : 'bg-red-50 dark:bg-red-500/15 text-red-600 dark:text-red-400'
                        }`}>
                          {r.status}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Fleet status */}
                <div className="rounded-xl border border-border bg-card p-6">
                  <h3 className="text-base font-semibold text-foreground mb-4" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Fleet Status</h3>
                  <div className="space-y-3">
                    {vehicles.map(v => (
                      <div key={v.vehicle_id} className="flex items-center gap-3 py-2 border-b border-border last:border-0">
                        <div className="w-9 h-9 rounded-lg bg-sky-50 dark:bg-sky-500/10 border border-sky-200 dark:border-sky-500/15 flex items-center justify-center">
                          <Bus className="w-4 h-4 text-sky-600 dark:text-sky-400" />
                        </div>
                        <div className="flex-1 min-w-0">
                          <p className="text-sm text-foreground font-medium">{v.plate_no}</p>
                          <p className="text-xs text-muted-foreground">{v.capacity} seats</p>
                        </div>
                        <div className="text-right">
                          <p className="text-xs text-muted-foreground">{v.driver_name || '—'}</p>
                          <div className={`text-xs px-2 py-0.5 rounded-full mt-1 ${v.driver_name ? 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400' : 'bg-muted text-muted-foreground'}`}>
                            {v.driver_name ? 'Assigned' : 'Unassigned'}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* ─── EMPLOYEES ─── */}
          {view === 'employees' && (
            <div className="space-y-6">
              <div className="flex items-center justify-between gap-4 flex-wrap">
                <SectionHeader
                  icon={Users}
                  iconColor="bg-sky-50 dark:bg-sky-500/15 text-sky-600 dark:text-sky-400"
                  title="Employees"
                  subtitle="Manage employee accounts and access"
                  count={filteredEmployees.length}
                />
                <div className="flex items-center gap-3">
                  <div className="relative">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                    <input
                      value={searchQ}
                      onChange={e => setSearchQ(e.target.value)}
                      placeholder="Search employees..."
                      className="w-64 pl-10 pr-4 py-2.5 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                    />
                  </div>
                  <button
                    onClick={() => setAddEmpOpen(true)}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 text-sm font-semibold transition"
                  >
                    <Plus className="w-4 h-4" />
                    Add Employee
                  </button>
                </div>
              </div>

              {filteredEmployees.length === 0 ? (
                <div className="rounded-xl border border-border bg-card text-center py-12 text-muted-foreground">No employees found.</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
                  {filteredEmployees.map((emp) => (
                    <div key={emp.id} className="rounded-xl border border-border bg-card p-5 hover:shadow-md hover:border-foreground/15 transition-all">
                      <div className="flex items-start justify-between gap-3">
                        <div className="flex items-center gap-3 min-w-0">
                          <div className="w-11 h-11 rounded-full bg-sky-50 dark:bg-sky-500/15 ring-2 ring-sky-100 dark:ring-sky-500/10 flex items-center justify-center text-sm font-bold text-sky-600 dark:text-sky-400 flex-shrink-0">
                            {emp.name.split(' ').map(n => n[0]).join('').slice(0, 2)}
                          </div>
                          <div className="min-w-0">
                            <p className="text-sm font-semibold text-foreground truncate">{emp.name}</p>
                            <p className="text-xs text-muted-foreground font-mono">ID {emp.employeeId || '—'}</p>
                          </div>
                        </div>
                        <StatusPill active={emp.status !== 'Inactive'} activeLabel="Active" inactiveLabel="Inactive" />
                      </div>

                      <div className="mt-4 pt-4 border-t border-border space-y-1.5">
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Mail className="w-3.5 h-3.5 flex-shrink-0" />
                          <span className="truncate">{emp.email}</span>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Phone className="w-3.5 h-3.5 flex-shrink-0" />
                          <span>{emp.phone || '—'}</span>
                        </div>
                      </div>

                      <div className="mt-4 pt-3 border-t border-border flex items-center justify-end gap-1">
                        <button
                          onClick={() => setViewEmpDetail(emp)}
                          className="p-1.5 rounded-lg hover:bg-muted text-muted-foreground hover:text-foreground transition"
                          title="View"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => setResetPwdUser(emp)}
                          className="p-1.5 rounded-lg hover:bg-amber-50 dark:hover:bg-amber-500/10 text-muted-foreground hover:text-amber-600 dark:hover:text-amber-400 transition"
                          title="Reset Password"
                        >
                          <Key className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => setDeleteUser(emp)}
                          className="p-1.5 rounded-lg hover:bg-red-50 dark:hover:bg-red-500/10 text-muted-foreground hover:text-red-600 dark:hover:text-red-400 transition"
                          title="Delete"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ─── DRIVERS ─── */}
          {view === 'drivers' && (
            <div className="space-y-6">
              <SectionHeader
                icon={Truck}
                iconColor="bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400"
                title="Drivers"
                subtitle="Fleet drivers and their vehicle assignments"
                count={drivers.length}
              />
              {drivers.length === 0 ? (
                <div className="rounded-xl border border-border bg-card text-center py-12 text-muted-foreground">No drivers found.</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
                  {drivers.map((drv) => {
                    const vehicle = vehicles.find(v => v.driver_id === drv.driver_id);
                    return (
                      <div key={drv.driver_id} className="rounded-xl border border-border bg-card p-5 hover:shadow-md hover:border-foreground/15 transition-all">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="w-11 h-11 rounded-full bg-emerald-50 dark:bg-emerald-500/15 ring-2 ring-emerald-100 dark:ring-emerald-500/10 flex items-center justify-center text-sm font-bold text-emerald-600 dark:text-emerald-400 flex-shrink-0">
                              {drv.name.split(' ').map(n => n[0]).join('').slice(0, 2)}
                            </div>
                            <div className="min-w-0">
                              <p className="text-sm font-semibold text-foreground truncate">{drv.name}</p>
                              <p className="text-xs text-muted-foreground font-mono">{drv.license_no}</p>
                            </div>
                          </div>
                          <StatusPill
                            active={drv.status === 'Active' || drv.status === 'Available'}
                            activeLabel={drv.status}
                            inactiveLabel={drv.status || 'Unknown'}
                          />
                        </div>

                        <div className="mt-4 pt-4 border-t border-border">
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold mb-2">Assigned Vehicle</p>
                          {vehicle ? (
                            <div className="flex items-center gap-2">
                              <div className="w-8 h-8 rounded-lg bg-sky-50 dark:bg-sky-500/10 flex items-center justify-center flex-shrink-0">
                                <Bus className="w-4 h-4 text-sky-600 dark:text-sky-400" />
                              </div>
                              <div>
                                <p className="text-sm text-foreground font-medium">{vehicle.plate_no}</p>
                                <p className="text-xs text-muted-foreground">{vehicle.capacity} seats</p>
                              </div>
                            </div>
                          ) : <p className="text-sm text-muted-foreground">Unassigned</p>}
                        </div>

                        <div className="mt-3 pt-3 border-t border-border space-y-1.5">
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Mail className="w-3.5 h-3.5 flex-shrink-0" />
                            <span className="truncate">{drv.email}</span>
                          </div>
                          <div className="flex items-center gap-2 text-xs text-muted-foreground">
                            <Phone className="w-3.5 h-3.5 flex-shrink-0" />
                            <span>{drv.phone || '—'}</span>
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ─── VEHICLES ─── */}
          {view === 'vehicles' && (
            <div className="space-y-6">
              <SectionHeader
                icon={Car}
                iconColor="bg-[#14B8A6]/15 text-[#14B8A6]"
                title="Vehicles"
                subtitle="Fleet vehicle roster and status"
                count={vehicles.length}
              />
              {vehicles.length === 0 ? (
                <div className="rounded-xl border border-border bg-card text-center py-12 text-muted-foreground">No vehicles found.</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
                  {vehicles.map((v) => {
                    const driver = drivers.find(d => d.driver_id === v.driver_id);
                    return (
                      <div key={v.vehicle_id} className="rounded-xl border border-border bg-card p-5 hover:shadow-md hover:border-foreground/15 transition-all">
                        <div className="flex items-start justify-between gap-3">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className="w-11 h-11 rounded-xl bg-sky-50 dark:bg-sky-500/10 ring-2 ring-sky-100 dark:ring-sky-500/10 flex items-center justify-center flex-shrink-0">
                              <Bus className="w-5 h-5 text-sky-600 dark:text-sky-400" />
                            </div>
                            <div className="min-w-0">
                              <p className="text-sm font-semibold font-mono text-foreground leading-snug break-words">{v.plate_no}</p>
                              <p className="text-xs text-muted-foreground">{v.capacity} seats</p>
                            </div>
                          </div>
                          <span className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-full font-medium flex-shrink-0 ${
                            v.status === 'Active' ? 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                            : v.status === 'Maintenance' ? 'bg-amber-50 dark:bg-amber-500/15 text-amber-600 dark:text-amber-400'
                            : 'bg-muted text-muted-foreground'
                          }`}>
                            <span className={`w-1.5 h-1.5 rounded-full ${
                              v.status === 'Active' ? 'bg-emerald-500' : v.status === 'Maintenance' ? 'bg-amber-500' : 'bg-muted-foreground/50'
                            }`} />
                            {v.status}
                          </span>
                        </div>

                        <div className="mt-4 pt-4 border-t border-border">
                          <p className="text-[10px] uppercase tracking-wider text-muted-foreground font-semibold mb-1.5">Assigned Driver</p>
                          <p className="text-sm text-foreground">{driver?.name || <span className="text-muted-foreground">Unassigned</span>}</p>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          )}

          {/* ─── REQUESTS ─── */}
          {view === 'requests' && (
            <div className="space-y-6">
              <div className="flex items-center gap-3 flex-wrap">
                <div className="flex items-center gap-2">
                  <Filter className="w-4 h-4 text-muted-foreground" />
                  <span className="text-sm text-muted-foreground">Date:</span>
                </div>
                <input
                  type="date"
                  value={selectedDateFilter}
                  onChange={e => setSelectedDateFilter(e.target.value)}
                  className="px-3 py-2 rounded-lg border border-border bg-muted text-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                />
                <span className="text-xs text-muted-foreground ml-auto">
                  {requestsLoading ? 'Loading…' : `${dateRequests.length} request(s)`}
                </span>
              </div>

              {requestsLoading ? (
                <div className="rounded-xl border border-border bg-card text-center py-12 text-muted-foreground">Loading requests for {selectedDateFilter}…</div>
              ) : dateRequests.length === 0 ? (
                <div className="rounded-xl border border-border bg-card text-center py-12 text-muted-foreground">No requests for {selectedDateFilter}.</div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4 items-start">
                  {dateRequests.map((r) => (
                    <div key={`${r.kind}-${r.id}`} className="rounded-xl border border-border bg-card p-5 hover:shadow-md hover:border-foreground/15 transition-all">
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <p className="text-sm font-semibold text-foreground truncate">{r.employeeName || `Employee #${r.employeeId}`}</p>
                          <span className={`inline-block mt-1.5 text-xs px-2 py-0.5 rounded-full font-medium capitalize ${r.kind === 'pickup' ? 'bg-sky-50 dark:bg-sky-500/15 text-sky-600 dark:text-sky-400' : 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'}`}>
                            {r.kind}
                          </span>
                        </div>
                        <span className={`text-xs px-2.5 py-1 rounded-full font-medium flex-shrink-0 ${
                          r.status === 'Approved' ? 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'
                          : r.status === 'Pending' ? 'bg-amber-50 dark:bg-amber-500/15 text-amber-600 dark:text-amber-400'
                          : 'bg-red-50 dark:bg-red-500/15 text-red-600 dark:text-red-400'
                        }`}>
                          {r.status}
                        </span>
                      </div>

                      <div className="mt-4 pt-4 border-t border-border space-y-1.5">
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <MapPin className="w-3.5 h-3.5 flex-shrink-0" />
                          <span className="truncate">{r.zoneName || `${r.lat ?? '—'}, ${r.lng ?? '—'}`}</span>
                        </div>
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <Clock className="w-3.5 h-3.5 flex-shrink-0" />
                          <span>{r.serviceDate} · <span className="font-mono">{r.shiftTime || '—'}</span></span>
                        </div>
                      </div>

                      {r.status === 'Pending' && (
                        <div className="mt-4 pt-3 border-t border-border flex gap-2">
                          <button
                            onClick={() => r.kind === 'pickup' ? approvePickup(r.id) : approveDropoff(r.id)}
                            disabled={requestActionLoading}
                            className="flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 hover:bg-emerald-100 dark:hover:bg-emerald-500/25 transition disabled:opacity-40"
                          >
                            Approve
                          </button>
                          <button
                            onClick={() => r.kind === 'pickup' ? rejectPickup(r.id) : rejectDropoff(r.id)}
                            disabled={requestActionLoading}
                            className="flex-1 px-3 py-1.5 rounded-lg text-xs font-semibold bg-red-50 dark:bg-red-500/15 text-red-600 dark:text-red-400 hover:bg-red-100 dark:hover:bg-red-500/25 transition disabled:opacity-40"
                          >
                            Reject
                          </button>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* ─── ROUTING ─── */}
          {view === 'routing' && (
            <div className="space-y-6">
              {/* Routing controls */}
              <div className="rounded-xl border border-border bg-card p-6">
                <h3 className="text-base font-semibold text-foreground mb-4" style={{ fontFamily: 'Rajdhani, sans-serif' }}>
                  Routing
                </h3>
                <p className="text-sm text-muted-foreground mb-6">
                  Routing runs automatically once the weekly request window closes. Pick a day to
                  preview request counts, view the routes the solver produced, or force a re-route.
                </p>

                {/* Date selector */}
                <div className="flex items-end gap-4 flex-wrap mb-6">
                  <div>
                    <label className="block text-xs text-muted-foreground mb-2 uppercase tracking-wider">Date</label>
                    <input
                      type="date"
                      value={routingDate}
                      onChange={e => { setRoutingDate(e.target.value); setPickupPreview(null); setDropoffPreview(null); setRoutingResult(null); setRunDayMessage(null); }}
                      className="px-3 py-2.5 rounded-lg border border-border bg-muted text-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                    />
                  </div>
                </div>

                {/* Action buttons */}
                <div className="flex items-center gap-3 flex-wrap">
                  <button
                    onClick={() => loadPreview('pickup')}
                    disabled={apiLoading}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-sky-200 dark:border-sky-500/30 text-sky-600 dark:text-sky-400 hover:bg-sky-50 dark:hover:bg-sky-500/10 text-sm font-medium transition disabled:opacity-60"
                  >
                    <Eye className="w-4 h-4" />
                    Preview Pickup
                  </button>
                  <button
                    onClick={() => loadPreview('dropoff')}
                    disabled={apiLoading}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-sky-200 dark:border-sky-500/30 text-sky-600 dark:text-sky-400 hover:bg-sky-50 dark:hover:bg-sky-500/10 text-sm font-medium transition disabled:opacity-60"
                  >
                    <Eye className="w-4 h-4" />
                    Preview Dropoff
                  </button>
                  <button
                    onClick={loadScheduleSummary}
                    disabled={apiLoading}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-[#14B8A6]/30 text-[#14B8A6] hover:bg-[#14B8A6]/10 text-sm font-medium transition disabled:opacity-60"
                  >
                    <BarChart3 className="w-4 h-4" />
                    View Results
                  </button>
                  <button
                    onClick={runDayRouting}
                    disabled={isRunningDay || apiLoading}
                    className="flex items-center gap-2 px-4 py-2.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 text-sm font-semibold transition disabled:opacity-60 disabled:cursor-not-allowed ml-auto"
                  >
                    {isRunningDay ? (
                      <>
                        <div className="w-4 h-4 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full animate-spin" />
                        Re-routing {routingDate}...
                      </>
                    ) : (
                      <>
                        <Route className="w-4 h-4" />
                        Re-route This Day
                      </>
                    )}
                  </button>
                </div>

                {runDayMessage && (
                  <div className="mt-4 flex items-center gap-2 text-sm text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10 rounded-lg px-4 py-3">
                    <CheckCircle className="w-4 h-4 flex-shrink-0" />
                    {runDayMessage}
                  </div>
                )}
              </div>

              {/* Preview panels */}
              {(pickupPreview || dropoffPreview) && (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {pickupPreview && (
                    <div className="rounded-xl border border-sky-200 dark:border-sky-500/20 bg-card p-5">
                      <h4 className="text-sm font-semibold text-sky-600 dark:text-sky-400 mb-3" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Pickup Preview</h4>
                      <div className="grid grid-cols-2 gap-3 text-sm">
                        <div><span className="text-muted-foreground">Total:</span> <span className="text-foreground font-semibold">{pickupPreview.total_requests}</span></div>
                        <div><span className="text-amber-600 dark:text-amber-400">Pending:</span> <span className="text-foreground font-semibold">{pickupPreview.pending}</span></div>
                        <div><span className="text-emerald-600 dark:text-emerald-400">Approved:</span> <span className="text-foreground font-semibold">{pickupPreview.approved}</span></div>
                        <div><span className="text-red-600 dark:text-red-400">Rejected:</span> <span className="text-foreground font-semibold">{pickupPreview.rejected}</span></div>
                      </div>
                    </div>
                  )}
                  {dropoffPreview && (
                    <div className="rounded-xl border border-emerald-200 dark:border-emerald-500/20 bg-card p-5">
                      <h4 className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 mb-3" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Dropoff Preview</h4>
                      <div className="grid grid-cols-2 gap-3 text-sm">
                        <div><span className="text-muted-foreground">Total:</span> <span className="text-foreground font-semibold">{dropoffPreview.total_requests}</span></div>
                        <div><span className="text-amber-600 dark:text-amber-400">Pending:</span> <span className="text-foreground font-semibold">{dropoffPreview.pending}</span></div>
                        <div><span className="text-emerald-600 dark:text-emerald-400">Approved:</span> <span className="text-foreground font-semibold">{dropoffPreview.approved}</span></div>
                        <div><span className="text-red-600 dark:text-red-400">Rejected:</span> <span className="text-foreground font-semibold">{dropoffPreview.rejected}</span></div>
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Schedule results */}
              {routingResult && routingResult.routes.length > 0 && (
                <div className="space-y-4">
                  <div className="flex items-center gap-3">
                    <MapPin className="w-5 h-5 text-sky-600 dark:text-sky-400" />
                    <h3 className="text-base font-semibold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>
                      Routes — {routingDate}
                    </h3>
                    <span className="text-xs text-muted-foreground">{routingResult.routes.length} route(s)</span>
                  </div>

                  {routingResult.routes.map((route) => {
                    const vehicle = vehicles.find(v => v.vehicle_id === route.assignment?.vehicle_id);
                    const driver = drivers.find(d => d.driver_id === route.assignment?.driver_id);
                    const sortedStops = [...route.stops].sort((a, b) => a.sequence_order - b.sequence_order);

                    return (
                      <div key={route.route_id} className="rounded-xl border border-border bg-card overflow-hidden">
                        <div className="p-5 border-b border-border">
                          <div className="flex items-start justify-between">
                            <div>
                              <div className="flex items-center gap-2 mb-1">
                                <span className="text-xs font-mono text-sky-600 dark:text-sky-400 bg-sky-50 dark:bg-sky-500/10 px-2 py-0.5 rounded">Route #{route.route_id}</span>
                                <span className={`text-xs px-2 py-0.5 rounded-full ${route.route_type === 'pickup' ? 'bg-sky-50 dark:bg-sky-500/15 text-sky-600 dark:text-sky-400' : 'bg-emerald-50 dark:bg-emerald-500/15 text-emerald-600 dark:text-emerald-400'}`}>
                                  {route.route_type}
                                </span>
                              </div>
                              <p className="text-sm text-foreground font-medium mt-1">{vehicle?.plate_no || 'Unassigned'}</p>
                              <p className="text-xs text-muted-foreground">Driver: {driver?.name || '—'}</p>
                            </div>
                            <div className="text-right text-xs text-muted-foreground">
                              <p>{route.total_distance_km?.toFixed(1)} km</p>
                              <p>{route.total_travel_time_min} min</p>
                            </div>
                          </div>
                        </div>

                        {/* Stops (left) + map (right) — same side-by-side convention as the Employee route view */}
                        <div className="grid grid-cols-1 lg:grid-cols-5 gap-5 p-5">
                          <div className="lg:col-span-2 space-y-2">
                            {sortedStops.map((stop, idx) => (
                              <div key={stop.stop_id} className="flex items-start gap-3">
                                <div className="flex flex-col items-center mt-1">
                                  <div className={`w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold text-white ${stop.passengers.length === 0 ? 'bg-emerald-500' : 'bg-sky-500'}`}>
                                    {stop.sequence_order}
                                  </div>
                                  {idx < sortedStops.length - 1 && (
                                    <div className="w-px flex-1 bg-border mt-1 h-5" />
                                  )}
                                </div>
                                <div className="flex-1 pb-2">
                                  <div className="flex items-center gap-2 flex-wrap">
                                    <p className="text-sm text-foreground font-medium">
                                      {stop.stop_name || (stop.passengers.length === 0 ? 'Office' : `${stop.passengers.length} passenger(s)`)}
                                    </p>
                                    {stop.is_shared && (
                                      <span className="text-xs px-1.5 py-0.5 rounded bg-violet-50 dark:bg-violet-500/15 text-violet-700 dark:text-violet-300 font-medium">
                                        shared drop
                                      </span>
                                    )}
                                    {stop.is_adhoc && (
                                      <span className="text-xs px-1.5 py-0.5 rounded bg-amber-50 dark:bg-amber-500/15 text-amber-700 dark:text-amber-300 font-medium">
                                        ad-hoc
                                      </span>
                                    )}
                                  </div>
                                  {stop.passengers.length > 0 && (
                                    <p className="text-xs text-muted-foreground">
                                      {stop.passengers.map(p => p.employee_name || `Employee #${p.employee_id}`).join(', ')}
                                    </p>
                                  )}
                                  <p className="text-xs text-muted-foreground font-mono">{stop.arrival_time || '—'} → {stop.departure_time || '—'}</p>
                                  <p className="text-xs text-muted-foreground/70">{stop.latitude.toFixed(5)}, {stop.longitude.toFixed(5)}</p>
                                </div>
                              </div>
                            ))}
                          </div>

                          <div className="lg:col-span-3">
                            <p className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">Route Map</p>
                            <InteractiveMap
                              center={sortedStops.length > 0 ? [sortedStops[0].latitude, sortedStops[0].longitude] : [OFFICE_LOCATION.latitude, OFFICE_LOCATION.longitude]}
                              markers={buildDriverStopMarkers(sortedStops, route.route_type, route.route_geometry)}
                              fitToMarkers
                              showRoute
                              routeGeometry={route.route_geometry}
                              height="320px"
                              lazy
                            />
                            <MapLegend showMine={false} routeType={route.route_type === 'dropoff' ? 'dropoff' : 'pickup'} />
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}

              {routingResult && routingResult.routes.length === 0 && (
                <div className="rounded-xl border border-border bg-card p-8 text-center">
                  <AlertCircle className="w-8 h-8 text-muted-foreground mx-auto mb-3" />
                  <p className="text-muted-foreground">No routes found for this date and shift.</p>
                </div>
              )}
            </div>
          )}
        </div>
        </div>
      </main>

      {/* ─── MODALS ─── */}

      {/* Add Employee Modal */}
      {addEmpOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 shadow-2xl">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-lg font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Add New Employee</h3>
              <button onClick={() => setAddEmpOpen(false)} className="text-muted-foreground hover:text-foreground transition">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="space-y-4">
              {[
                { label: 'Full Name', field: 'name', placeholder: 'e.g. Rafiqul Islam', type: 'text' },
                { label: 'Email', field: 'email', placeholder: 'email@company.com', type: 'email' },
                { label: 'Phone', field: 'phone', placeholder: '+880-17xx-xxxxxx', type: 'text' },
                { label: 'Temporary Password', field: 'password', placeholder: 'Set initial password', type: 'password' },
              ].map(({ label, field, placeholder, type }) => (
                <div key={field}>
                  <label className="block text-xs text-muted-foreground mb-1.5 uppercase tracking-wider">{label}</label>
                  <input
                    type={type}
                    value={(newEmp as any)[field]}
                    onChange={e => setNewEmp(prev => ({ ...prev, [field]: e.target.value }))}
                    placeholder={placeholder}
                    className="w-full px-3 py-2.5 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition"
                  />
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-3">
              Give the temporary password to the employee offline. They can change it after logging in.
            </p>
            <div className="flex gap-3 mt-6">
              <button onClick={() => setAddEmpOpen(false)} className="flex-1 py-2.5 rounded-lg border border-border text-muted-foreground hover:text-foreground hover:border-foreground/20 text-sm transition">
                Cancel
              </button>
              <button
                onClick={handleAddEmployee}
                disabled={addSaving}
                className="flex-1 py-2.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 font-semibold text-sm transition disabled:opacity-60"
              >
                {addSaving ? 'Adding…' : 'Add Employee'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Reset Password Modal */}
      {resetPwdUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-8 shadow-2xl">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-lg font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Reset Password</h3>
              <button onClick={() => setResetPwdUser(null)} className="text-muted-foreground hover:text-foreground transition">
                <X className="w-5 h-5" />
              </button>
            </div>
            <p className="text-sm text-muted-foreground mb-4">
              Set a new temporary password for <span className="text-foreground font-medium">{resetPwdUser.name}</span>.
              They'll need to log in with this — you can't see their current password.
            </p>
            <input
              type="password"
              value={resetPwdNew}
              onChange={e => { setResetPwdNew(e.target.value); setResetPwdError(''); }}
              placeholder="New password (at least 6 characters)"
              className="w-full px-3 py-2.5 rounded-lg border border-border bg-muted text-foreground placeholder:text-muted-foreground text-sm focus:outline-none focus:border-[#14B8A6]/50 transition mb-2"
            />
            {resetPwdError && <p className="text-xs text-red-600 dark:text-red-400 mb-4">{resetPwdError}</p>}
            <div className="flex gap-3">
              <button onClick={() => setResetPwdUser(null)} className="flex-1 py-2.5 rounded-lg border border-border text-muted-foreground text-sm hover:text-foreground hover:border-foreground/20 transition">
                Cancel
              </button>
              <button
                onClick={handleResetPassword}
                disabled={resetSaving}
                className="flex-1 py-2.5 rounded-lg bg-primary text-primary-foreground hover:opacity-90 font-semibold text-sm transition disabled:opacity-60"
              >
                {resetSaving ? 'Resetting…' : 'Reset Password'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete Confirm Modal */}
      {deleteUser && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-sm rounded-2xl border border-border bg-card p-8 shadow-2xl">
            <div className="flex items-center gap-3 mb-4">
              <div className="w-10 h-10 rounded-full bg-red-50 dark:bg-red-500/15 border border-red-200 dark:border-red-500/20 flex items-center justify-center">
                <Trash2 className="w-5 h-5 text-red-600 dark:text-red-400" />
              </div>
              <h3 className="text-lg font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Delete Employee</h3>
            </div>
            <p className="text-sm text-muted-foreground mb-6">
              Deactivate <span className="text-foreground font-medium">{deleteUser.name}</span>? They won't be able to log
              in, and their account can be re-enabled later. Their request history is kept.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setDeleteUser(null)} className="flex-1 py-2.5 rounded-lg border border-border text-muted-foreground text-sm hover:text-foreground hover:border-foreground/20 transition">
                Cancel
              </button>
              <button
                onClick={handleDeleteEmployee}
                disabled={actionSaving}
                className="flex-1 py-2.5 rounded-lg bg-red-500 hover:bg-red-400 text-white font-semibold text-sm transition disabled:opacity-60"
              >
                {actionSaving ? 'Deactivating…' : 'Deactivate'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Employee Detail Modal */}
      {viewEmpDetail && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 shadow-2xl">
            <div className="flex items-center justify-between mb-6">
              <h3 className="text-lg font-bold text-foreground" style={{ fontFamily: 'Rajdhani, sans-serif' }}>Employee Details</h3>
              <button onClick={() => setViewEmpDetail(null)} className="text-muted-foreground hover:text-foreground transition">
                <X className="w-5 h-5" />
              </button>
            </div>
            <div className="flex items-center gap-4 mb-6 pb-6 border-b border-border">
              <div className="w-14 h-14 rounded-full bg-sky-50 dark:bg-sky-500/15 border border-sky-200 dark:border-sky-500/20 flex items-center justify-center text-xl font-bold text-sky-600 dark:text-sky-400">
                {viewEmpDetail.name.split(' ').map(n => n[0]).join('').slice(0, 2)}
              </div>
              <div>
                <p className="text-lg font-bold text-foreground">{viewEmpDetail.name}</p>
                <p className="text-sm text-muted-foreground">ID {viewEmpDetail.employeeId}</p>
              </div>
            </div>
            <div className="space-y-3">
              {[
                { icon: Mail, label: 'Email', value: viewEmpDetail.email },
                { icon: Phone, label: 'Phone', value: viewEmpDetail.phone || '—' },
              ].map(({ icon: Icon, label, value }) => (
                <div key={label} className="flex items-start gap-3">
                  <Icon className="w-4 h-4 text-muted-foreground mt-0.5" />
                  <div>
                    <p className="text-xs text-muted-foreground uppercase tracking-wider">{label}</p>
                    <p className="text-sm text-foreground">{value}</p>
                  </div>
                </div>
              ))}
            </div>
            <button onClick={() => setViewEmpDetail(null)} className="w-full mt-6 py-2.5 rounded-lg border border-border text-muted-foreground text-sm hover:text-foreground hover:border-foreground/20 transition">
              Close
            </button>
          </div>
        </div>
      )}
    </div>
  );
};
