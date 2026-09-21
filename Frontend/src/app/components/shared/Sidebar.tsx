import React, { ReactNode } from 'react';
import { Link, useLocation, useNavigate } from 'react-router';
import {
  MapPinned, ClipboardList, User, LogOut, Map,
  Menu, X, Zap, Bus,
} from 'lucide-react';
import { useAuth } from '../../context/AuthContext';

interface SidebarProps {
  role: 'employee' | 'driver';
  children: ReactNode;
}

interface NavItem {
  icon: React.ElementType;
  label: string;
  path: string;
  badge?: string;
}

const employeeNavItems: NavItem[] = [
  { icon: MapPinned, label: 'Pickup & Dropoff Request', path: '/employee/request' },
  { icon: Zap, label: 'Ad-hoc Request', path: '/employee/adhoc' },
  { icon: ClipboardList, label: 'My Requests', path: '/employee/requests' },
  { icon: User, label: 'My Profile', path: '/employee/profile' },
];

const driverNavItems: NavItem[] = [
  { icon: Map, label: "Today's Trips", path: '/driver/trips' },
  { icon: User, label: 'My Profile', path: '/driver/profile' },
];

// One muted, desaturated slate-blue — flat, no gradient. The editorial
// reference this follows uses solid color blocks, not shiny SaaS gradients.
const SIDEBAR_COLOR = '#3F4B5E';

export const Sidebar: React.FC<SidebarProps> = ({ role, children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const { user, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = React.useState(false);

  const navItems = role === 'employee' ? employeeNavItems : driverNavItems;

  const handleLogout = () => {
    logout();
    navigate('/login');
  };

  const SidebarContent = () => (
    <div className="flex flex-col h-full">
      {/* Logo */}
      <div className="px-5 py-6">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-white/10 border border-white/15 flex items-center justify-center">
            <Bus className="w-5 h-5 text-white" />
          </div>
          <div>
            <p className="font-bold text-white text-base tracking-tight" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              TranspoRT
            </p>
            <p className="text-xs text-slate-300 capitalize">{role} Portal</p>
          </div>
        </div>
      </div>

      {/* Nav — solid white pill for the active page against the flat
          colored rail, so the current page is unmistakable at a glance. */}
      <nav className="flex-1 px-3 pt-2 space-y-1 overflow-y-auto">
        {navItems.map(item => {
          const Icon = item.icon;
          const active = location.pathname === item.path;
          return (
            <Link
              key={item.path}
              to={item.path}
              onClick={() => setMobileOpen(false)}
            >
              <div
                className={`flex items-center gap-3 px-3.5 py-3 rounded-lg transition-all duration-150 ${
                  active
                    ? 'bg-white text-slate-800'
                    : 'text-slate-300 hover:text-white hover:bg-white/[0.08]'
                }`}
              >
                <Icon className={`w-[18px] h-[18px] flex-shrink-0 ${active ? 'text-slate-600' : 'text-slate-400'}`} />
                <span className={`text-sm font-medium flex-1 ${active ? 'text-slate-800' : ''}`}>{item.label}</span>
                {item.badge && (
                  <span className={`text-xs px-1.5 py-0.5 rounded font-semibold ${active ? 'bg-amber-100 text-amber-700' : 'bg-amber-400/20 text-amber-200'}`}>
                    {item.badge}
                  </span>
                )}
              </div>
            </Link>
          );
        })}
      </nav>

      {/* User card + sign out */}
      <div className="p-3 border-t border-white/10 mt-2">
        <div className="flex items-center gap-3 px-2.5 py-2.5 rounded-xl">
          <div className="w-9 h-9 rounded-full bg-white/10 border border-white/15 flex items-center justify-center text-sm font-bold text-white flex-shrink-0">
            {user?.name.split(' ').map(n => n[0]).join('').slice(0, 2)}
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-white truncate">{user?.name}</p>
            <p className="text-xs text-slate-300 truncate">{user?.email}</p>
          </div>
        </div>
        <button
          onClick={handleLogout}
          className="w-full flex items-center gap-3 px-3 py-2.5 mt-1 rounded-lg text-sm text-slate-300 hover:text-white hover:bg-white/[0.08] transition"
        >
          <LogOut className="w-4 h-4" />
          <span className="font-medium">Sign Out</span>
        </button>
      </div>
    </div>
  );

  return (
    <div className="flex h-screen bg-background">
      {/* Desktop Sidebar — flat, no gradient/glow, matching the reference's
          solid color-block language. */}
      <aside className="hidden md:flex md:flex-col w-64 flex-shrink-0" style={{ background: SIDEBAR_COLOR }}>
        <SidebarContent />
      </aside>

      {/* Mobile topbar */}
      <div className="md:hidden fixed top-0 left-0 right-0 z-40 flex items-center justify-between px-4 py-3 shadow-sm" style={{ background: SIDEBAR_COLOR }}>
        <div className="flex items-center gap-2">
          <div className="w-8 h-8 rounded-lg bg-white/10 border border-white/15 flex items-center justify-center">
            <Bus className="w-4 h-4 text-white" />
          </div>
          <span className="font-bold text-white tracking-tight" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>TranspoRT</span>
        </div>
        <button
          onClick={() => setMobileOpen(!mobileOpen)}
          className="text-slate-300 hover:text-white transition"
        >
          {mobileOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {/* Mobile sidebar overlay */}
      {mobileOpen && (
        <>
          <div
            className="md:hidden fixed inset-0 bg-stone-900/40 z-40"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="md:hidden fixed top-0 left-0 bottom-0 w-64 z-50 shadow-2xl" style={{ background: SIDEBAR_COLOR }}>
            <SidebarContent />
          </aside>
        </>
      )}

      {/* Content */}
      <main className="flex-1 overflow-y-auto pt-14 md:pt-0">
        {children}
      </main>
    </div>
  );
};
