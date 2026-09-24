import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ThemeProvider } from './context/ThemeContext';
import { Toaster } from './components/ui/sonner';

import { LoginPage } from './components/auth/LoginPage';
import { AdminLoginPage } from './components/auth/AdminLoginPage';
import { AdminDashboard } from './components/admin/AdminDashboard';

import { PickupDropoffRequestForm } from './components/employee/PickupDropoffRequestForm';
import { AdhocRequestForm } from './components/employee/AdhocRequestForm';
import { MyRequests } from './components/employee/MyRequests';
import { EmployeeProfile } from './components/employee/EmployeeProfile';

import { DriverTodaysTrips } from './components/driver/DriverTodaysTrips';
import { DriverProfile } from './components/driver/DriverProfile';

const ProtectedRoute: React.FC<{
  children: React.ReactNode;
  requiredRole?: 'employee' | 'driver' | 'admin';
}> = ({ children, requiredRole }) => {
  const { user } = useAuth();

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (requiredRole && user.role !== requiredRole) {
    if (user.role === 'employee') return <Navigate to="/employee/profile" replace />;
    if (user.role === 'driver') return <Navigate to="/driver/profile" replace />;
    if (user.role === 'admin') return <Navigate to="/admin" replace />;
  }

  return <>{children}</>;
};

const AppRoutes: React.FC = () => {
  const { user } = useAuth();

  return (
    <Routes>
      {/* Public */}
      <Route
        path="/login"
        element={
          !user ? (
            <LoginPage />
          ) : user.role === 'employee' ? (
            <Navigate to="/employee/profile" replace />
          ) : user.role === 'driver' ? (
            <Navigate to="/driver/profile" replace />
          ) : (
            <Navigate to="/admin" replace />
          )
        }
      />

      {/* Admin login — accessible only by direct URL */}
      <Route
        path="/login/admin"
        element={!user ? <AdminLoginPage /> : <Navigate to="/admin" replace />}
      />

      {/* Admin */}
      <Route
        path="/admin"
        element={
          <ProtectedRoute requiredRole="admin">
            <AdminDashboard />
          </ProtectedRoute>
        }
      />

      {/* Employee */}
      <Route
        path="/employee/profile"
        element={
          <ProtectedRoute requiredRole="employee">
            <EmployeeProfile />
          </ProtectedRoute>
        }
      />
      <Route
        path="/employee/request"
        element={
          <ProtectedRoute requiredRole="employee">
            <PickupDropoffRequestForm />
          </ProtectedRoute>
        }
      />
      <Route
        path="/employee/adhoc"
        element={
          <ProtectedRoute requiredRole="employee">
            <AdhocRequestForm />
          </ProtectedRoute>
        }
      />
      <Route
        path="/employee/requests"
        element={
          <ProtectedRoute requiredRole="employee">
            <MyRequests />
          </ProtectedRoute>
        }
      />

      {/* Driver */}
      <Route
        path="/driver/profile"
        element={
          <ProtectedRoute requiredRole="driver">
            <DriverProfile />
          </ProtectedRoute>
        }
      />
      <Route
        path="/driver/trips"
        element={
          <ProtectedRoute requiredRole="driver">
            <DriverTodaysTrips />
          </ProtectedRoute>
        }
      />

      {/* Root redirect */}
      <Route
        path="/"
        element={
          user ? (
            user.role === 'employee' ? (
              <Navigate to="/employee/profile" replace />
            ) : user.role === 'driver' ? (
              <Navigate to="/driver/profile" replace />
            ) : (
              <Navigate to="/admin" replace />
            )
          ) : (
            <Navigate to="/login" replace />
          )
        }
      />

      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
};

const App: React.FC = () => {
  return (
    <ThemeProvider>
      <AuthProvider>
        <BrowserRouter>
          <div className="min-h-screen bg-background">
            <AppRoutes />
            <Toaster />
          </div>
        </BrowserRouter>
      </AuthProvider>
    </ThemeProvider>
  );
};

export default App;
