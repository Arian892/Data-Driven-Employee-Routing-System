import React, { useState } from 'react';
import { useNavigate } from 'react-router';
import { Bus, Mail, Lock, Eye, EyeOff, Sun, Moon } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { useTheme } from '../../context/ThemeContext';
import { authApi } from '../../services/transportApi';
import { RouteMapBackdrop } from '../shared/RouteMapBackdrop';

export const LoginPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { login } = useAuth();
  const { theme, toggleTheme } = useTheme();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      const data = await authApi.login({ email, password });
      if (data.user.role === 'Admin') {
        throw new Error('Use the admin login page for admin accounts.');
      }

      const role = data.user.role === 'Driver' ? 'driver' : 'employee';
      login({
        id: String(data.user.user_id),
        name: data.user.name,
        email: data.user.email,
        phone: data.user.phone ?? '',
        role,
      });
      navigate(role === 'employee' ? '/employee/profile' : '/driver/profile');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid email or password. Contact your admin if you need access.');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background flex relative">
      {/* Theme toggle — visible before login too, top-right of the form side */}
      <button
        onClick={toggleTheme}
        className="absolute top-5 right-5 z-20 w-9 h-9 rounded-lg flex items-center justify-center border border-border bg-card text-muted-foreground hover:text-foreground transition"
        title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
      </button>

      {/* Left panel — branding, always Deep Navy (light theme) / near-black
          navy (dark theme) via the --sidebar token, same rail color as the
          logged-in app either way. */}
      <div
        className="hidden lg:flex lg:w-1/2 flex-col justify-between p-12 relative overflow-hidden"
        style={{ background: 'var(--sidebar)' }}
      >
        <RouteMapBackdrop />

        <div className="relative z-10">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-lg bg-white/10 border border-white/15 flex items-center justify-center">
              <Bus className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-white tracking-wide" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              TranspoRT
            </span>
          </div>
        </div>

        <div className="relative z-10 space-y-6">
          <div>
            <h1 className="text-4xl font-bold text-white leading-tight mb-4" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              TranspoRT: A <span style={{ color: '#14B8A6' }}>Data-Driven</span><br />
              Employee Routing System
            </h1>
            <p className="text-slate-400 text-lg leading-relaxed max-w-sm">
              Corporate transport route management for Dhaka&apos;s workforce. Request pickups, track routes, arrive on time.
            </p>
          </div>

          <div className="flex gap-6">
            <div className="text-center">
              <p className="text-3xl font-bold text-white" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>900+</p>
              <p className="text-slate-400 text-sm mt-1">Employees</p>
            </div>
            <div className="w-px bg-white/10" />
            <div className="text-center">
              <p className="text-3xl font-bold" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif', color: '#14B8A6' }}>40+</p>
              <p className="text-slate-400 text-sm mt-1">Vehicles</p>
            </div>
          </div>
        </div>

        <p className="relative z-10 text-slate-500 text-xs">© 2026 TranspoRT Systems. All rights reserved.</p>
      </div>

      {/* Right panel — form, on the theme's own page background */}
      <div className="flex-1 flex items-center justify-center p-8 bg-background">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-lg flex items-center justify-center" style={{ background: 'var(--sidebar)' }}>
              <Bus className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-foreground tracking-wide" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              TranspoRT
            </span>
          </div>

          <div className="mb-8">
            <h2 className="text-3xl font-bold text-foreground mb-2" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Sign In
            </h2>
            <p className="text-sm text-muted-foreground">
              Use the credentials provided by your administrator.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-xs font-semibold mb-2 uppercase tracking-wider text-muted-foreground">
                Email Address
              </label>
              <div className="relative">
                <Mail className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="your.email@company.com"
                  required
                  className="w-full pl-10 pr-4 py-3 rounded-xl border text-foreground placeholder:text-muted-foreground shadow-sm focus:outline-none focus:ring-2 transition"
                  style={{ background: 'var(--input)', borderColor: 'var(--border)' }}
                  onFocus={e => { e.currentTarget.style.borderColor = '#14B8A6'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(20,184,166,0.16)'; }}
                  onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = ''; }}
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-semibold mb-2 uppercase tracking-wider text-muted-foreground">
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                <input
                  type={showPass ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  required
                  className="w-full pl-10 pr-12 py-3 rounded-xl border text-foreground placeholder:text-muted-foreground shadow-sm focus:outline-none focus:ring-2 transition"
                  style={{ background: 'var(--input)', borderColor: 'var(--border)' }}
                  onFocus={e => { e.currentTarget.style.borderColor = '#14B8A6'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(20,184,166,0.16)'; }}
                  onBlur={e => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.boxShadow = ''; }}
                />
                <button
                  type="button"
                  onClick={() => setShowPass(!showPass)}
                  className="absolute right-3.5 top-1/2 -translate-y-1/2 transition text-muted-foreground hover:text-foreground"
                >
                  {showPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {error && (
              <div className="bg-red-50 dark:bg-red-500/10 border border-red-200 dark:border-red-500/20 text-red-600 dark:text-red-400 px-4 py-3 rounded-lg text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 rounded-lg font-semibold tracking-wide transition disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2 bg-primary text-primary-foreground hover:opacity-90"
            >
              {loading ? (
                <>
                  <div className="w-4 h-4 border-2 border-primary-foreground/30 border-t-primary-foreground rounded-full animate-spin" />
                  Signing in...
                </>
              ) : (
                'Sign In'
              )}
            </button>
          </form>
        </div>
      </div>
    </div>
  );
};
