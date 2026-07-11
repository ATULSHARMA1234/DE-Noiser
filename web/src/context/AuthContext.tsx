'use client';

import React, { createContext, useContext, useState, useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';

type User = {
 id: number;
 email: string;
 role: string;
};

type AuthContextType = {
 user: User | null;
 token: string | null;
 loading: boolean;
 logout: () => void;
 hasRole: (roles: string[]) => boolean;
};

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function AuthProvider({ children }: { children: React.ReactNode }) {
 const [user, setUser] = useState<User | null>(null);
 const [token, setToken] = useState<string | null>(null);
 const [loading, setLoading] = useState(true);
 const router = useRouter();
 const pathname = usePathname();

 useEffect(() => {
 // Read from localStorage on mount
 const storedToken = localStorage.getItem('token');
 const storedUser = localStorage.getItem('user');

 if (storedToken && storedUser) {
 setToken(storedToken);
 setUser(JSON.parse(storedUser));
 } else if (!pathname?.startsWith('/login') && pathname !== '/') {
 // If not logged in and trying to access app paths, redirect to login
 router.push('/login');
 }

 setLoading(false);
 }, [pathname, router]);

 const logout = () => {
 localStorage.removeItem('token');
 localStorage.removeItem('user');
 setUser(null);
 setToken(null);
 router.push('/login');
 };

 const hasRole = (roles: string[]) => {
 if (!user) return false;
 return roles.includes(user.role);
 };

 return (
 <AuthContext.Provider value={{ user, token, loading, logout, hasRole }}>
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
