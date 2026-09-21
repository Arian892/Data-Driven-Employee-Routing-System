import React, { useState } from 'react';
import { useNavigate } from 'react-router';
import { Bus, Mail, Lock, Eye, EyeOff, UserCircle, Truck } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
import { mockUsers } from '../../data/mockData';
import { authApi } from '../../services/transportApi';

export const LoginPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [showPass, setShowPass] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();
  const { login } = useAuth();

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

  const quickLogin = (role: 'employee' | 'driver') => {
    const u = mockUsers.find(u => u.role === role);
    if (u) {
      setEmail(u.email);
      setPassword(u.password || 'demo123');
    }
  };

  return (
    <div className="min-h-screen bg-background flex">
      {/* Left panel — branding */}
      <div
        className="hidden lg:flex lg:w-1/2 flex-col justify-between p-12 relative overflow-hidden"
        style={{
          background: 'linear-gradient(135deg, #FFFFFF 0%, #F8FAFC 50%, #F1F5F9 100%)',
        }}
      >
        {/* Decorative grid */}
        <div
          className="absolute inset-0 opacity-10"
          style={{
            backgroundImage: 'linear-gradient(rgba(14,165,233,0.3) 1px, transparent 1px), linear-gradient(90deg, rgba(14,165,233,0.3) 1px, transparent 1px)',
            backgroundSize: '60px 60px',
          }}
        />
        {/* Glow */}
        <div className="absolute top-1/3 left-1/2 -translate-x-1/2 -translate-y-1/2 w-96 h-96 bg-sky-500/10 rounded-full blur-3xl pointer-events-none" />

        <div className="relative z-10">
          <div className="flex items-center gap-3 mb-2">
            <div className="w-10 h-10 rounded-lg bg-sky-500/20 border border-sky-500/30 flex items-center justify-center">
              <Bus className="w-5 h-5 text-sky-700" />
            </div>
            <span className="font-rajdhani text-xl font-bold text-stone-900 tracking-wide" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              TranspoRT
            </span>
          </div>
        </div>

        <div className="relative z-10 space-y-6">
          <div>
            <h1 className="text-5xl font-bold text-stone-900 leading-tight mb-4" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Your Daily<br />
              <span className="text-sky-600">Commute,</span><br />
              Simplified.
            </h1>
            <p className="text-stone-600 text-lg leading-relaxed max-w-sm">
              Corporate transport route management for Dhaka&apos;s workforce. Request pickups, track routes, arrive on time.
            </p>
          </div>

          <div className="flex gap-6">
            <div className="text-center">
              <p className="text-3xl font-bold text-sky-600" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>500+</p>
              <p className="text-stone-500 text-sm mt-1">Employees</p>
            </div>
            <div className="w-px bg-stone-700" />
            <div className="text-center">
              <p className="text-3xl font-bold text-amber-600" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>24</p>
              <p className="text-stone-500 text-sm mt-1">Vehicles</p>
            </div>
            <div className="w-px bg-stone-700" />
            <div className="text-center">
              <p className="text-3xl font-bold text-emerald-600" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>98%</p>
              <p className="text-stone-500 text-sm mt-1">On-Time Rate</p>
            </div>
          </div>
        </div>

        <p className="relative z-10 text-stone-500 text-xs">© 2026 TranspoRT Systems. All rights reserved.</p>
      </div>

      {/* Right panel — form */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-lg bg-sky-500/20 border border-sky-500/30 flex items-center justify-center">
              <Bus className="w-5 h-5 text-sky-700" />
            </div>
            <span className="text-xl font-bold text-stone-900 tracking-wide" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              TranspoRT
            </span>
          </div>

          <div className="mb-8">
            <h2 className="text-3xl font-bold text-stone-900 mb-2" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Sign In
            </h2>
            <p className="text-stone-600 text-sm">
              Use the credentials provided by your administrator.
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-5">
            <div>
              <label className="block text-xs font-medium text-stone-600 mb-2 uppercase tracking-wider">
                Email Address
              </label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-stone-500" />
                <input
                  type="email"
                  value={email}
                  onChange={e => setEmail(e.target.value)}
                  placeholder="your.email@company.com"
                  required
                  className="w-full pl-10 pr-4 py-3 rounded-lg border border-stone-200 bg-stone-50 text-stone-900 placeholder:text-stone-500 focus:outline-none focus:border-sky-500/60 focus:ring-1 focus:ring-sky-500/30 transition"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-stone-600 mb-2 uppercase tracking-wider">
                Password
              </label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-stone-500" />
                <input
                  type={showPass ? 'text' : 'password'}
                  value={password}
                  onChange={e => setPassword(e.target.value)}
                  placeholder="Enter your password"
                  required
                  className="w-full pl-10 pr-12 py-3 rounded-lg border border-stone-200 bg-stone-50 text-stone-900 placeholder:text-stone-500 focus:outline-none focus:border-sky-500/60 focus:ring-1 focus:ring-sky-500/30 transition"
                />
                <button
                  type="button"
                  onClick={() => setShowPass(!showPass)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-stone-500 hover:text-stone-900 transition"
                >
                  {showPass ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {error && (
              <div className="bg-red-500/10 border border-red-500/20 text-red-700 px-4 py-3 rounded-lg text-sm">
                {error}
              </div>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full py-3 rounded-lg bg-slate-600 hover:bg-slate-500 text-white font-semibold tracking-wide transition disabled:opacity-60 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                  Signing in...
                </>
              ) : (
                'Sign In'
              )}
            </button>
          </form>

          {/* Demo quick login */}
          <div className="mt-8 pt-6 border-t border-stone-200">
            <p className="text-xs text-stone-500 text-center mb-4 uppercase tracking-wider">Demo Access</p>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => quickLogin('employee')}
                className="flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border border-stone-200 bg-stone-50 hover:bg-stone-100 text-stone-700 text-sm transition"
              >
                <UserCircle className="w-4 h-4" />
                Employee Demo
              </button>
              <button
                onClick={() => quickLogin('driver')}
                className="flex items-center justify-center gap-2 py-2.5 px-4 rounded-lg border border-stone-200 bg-stone-50 hover:bg-stone-100 text-stone-700 text-sm transition"
              >
                <Truck className="w-4 h-4" />
                Driver Demo
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
