import React, { useState } from 'react';
import { useNavigate } from 'react-router';
import { Bus, Mail, Lock, Eye, EyeOff } from 'lucide-react';
import { useAuth } from '../../context/AuthContext';
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

  return (
    <div className="min-h-screen bg-background flex">
      {/* Left panel — branding, same flat slate-blue as the logged-in app's sidebar/cards */}
      <div
        className="hidden lg:flex lg:w-1/2 flex-col justify-between p-12 relative overflow-hidden"
        style={{ background: '#3F4B5E' }}
      >
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
            <h1 className="text-5xl font-bold text-white leading-tight mb-4" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>
              Your Daily<br />
              <span className="text-amber-300">Commute,</span><br />
              Simplified.
            </h1>
            <p className="text-slate-300 text-lg leading-relaxed max-w-sm">
              Corporate transport route management for Dhaka&apos;s workforce. Request pickups, track routes, arrive on time.
            </p>
          </div>

          <div className="flex gap-6">
            <div className="text-center">
              <p className="text-3xl font-bold text-white" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>900+</p>
              <p className="text-slate-300 text-sm mt-1">Employees</p>
            </div>
            <div className="w-px bg-white/15" />
            <div className="text-center">
              <p className="text-3xl font-bold text-amber-300" style={{ fontFamily: 'Plus Jakarta Sans, sans-serif' }}>40+</p>
              <p className="text-slate-300 text-sm mt-1">Vehicles</p>
            </div>
          </div>
        </div>

        <p className="relative z-10 text-slate-400 text-xs">© 2026 TranspoRT Systems. All rights reserved.</p>
      </div>

      {/* Right panel — form */}
      <div className="flex-1 flex items-center justify-center p-8">
        <div className="w-full max-w-md">
          {/* Mobile logo */}
          <div className="lg:hidden flex items-center gap-3 mb-10">
            <div className="w-10 h-10 rounded-lg bg-[#3F4B5E] flex items-center justify-center">
              <Bus className="w-5 h-5 text-white" />
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
                  className="w-full pl-10 pr-4 py-3 rounded-lg border border-stone-200 bg-stone-50 text-stone-900 placeholder:text-stone-500 focus:outline-none focus:border-slate-500/60 focus:ring-1 focus:ring-slate-500/30 transition"
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
                  className="w-full pl-10 pr-12 py-3 rounded-lg border border-stone-200 bg-stone-50 text-stone-900 placeholder:text-stone-500 focus:outline-none focus:border-slate-500/60 focus:ring-1 focus:ring-slate-500/30 transition"
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
        </div>
      </div>
    </div>
  );
};
