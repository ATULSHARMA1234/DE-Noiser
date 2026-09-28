'use client';

import React, { createContext, useContext, useState, useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { apiFetch, apiPost, setSessionToken } from '@/lib/api';

type User = {
 id: number;
 email: string;
 role: string;
};

type AuthContextType = {
 user: User | null;
 loading: boolean;
 logout: () => void;
 hasRole: (roles: string[]) => boolean;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
 const [user, setUser] = useState<User | null>(null);
 const [loading, setLoading] = useState(true);
 const router = useRouter();
 const pathname = usePathname();

 useEffect(() => {
 // The session lives in httpOnly cookies, which JavaScript cannot read, so
 // the only way to know whether one is active is to ask the server. The
 // cached profile just avoids a blank frame while that call is in flight.
 const storedUser = localStorage.getItem('user');
 if (storedUser) {
 try {
 setUser(JSON.parse(storedUser));
 } catch {
 localStorage.removeItem('user');
 }
 }

 let cancelled = false;
 apiFetch('/auth/me')
 .then((me) => {
 if (cancelled) return;
 setUser(me);
 localStorage.setItem('user', JSON.stringify(me));
 })
 .catch(() => {
 if (cancelled) return;
 // apiFetch already tried a refresh and redirected if that failed.
 setUser(null);
 localStorage.removeItem('user');
 if (!pathname?.startsWith('/login') && pathname !== '/') {
 router.push('/login');
 }
 })
 .finally(() => {
 if (!cancelled) setLoading(false);
 });

 return () => { cancelled = true; };
 }, [pathname, router]);

 const logout = async () => {
 // Ask the server to revoke the tokens and clear the cookies. Dropping the
 // client's copy alone would leave a valid session the browser still holds.
 try {
 await apiPost('/auth/logout', {});
 } catch {
 // Already expired or unreachable; clear locally regardless.
 }
 setSessionToken(null);
 localStorage.removeItem('user');
 localStorage.removeItem('token');
 setUser(null);
 router.push('/login');
 };

 const hasRole = (roles: string[]) => {
 if (!user) return false;
 return roles.includes(user.role);
 };

 return (
 <AuthContext.Provider value={{ user, loading, logout, hasRole }}>
 {children}
 </AuthContext.Provider>
 );
}

export function useAuth() {
 const context = useContext(AuthContext);
 if (context === undefined) {
 throw new Error('useAuth must be used within an AuthProvider');
 }
 return context;
}
