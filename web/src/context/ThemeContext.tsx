'use client';

import React, { createContext, useContext, useState, useEffect } from 'react';

type Theme = 'dark' | 'light';

type ThemeContextType = {
 theme: Theme;
 toggleTheme: () => void;
};

const ThemeContext = createContext<ThemeContextType | undefined>(undefined);

export function ThemeProvider({ children }: { children: React.ReactNode }) {
 const [theme, setTheme] = useState<Theme>('dark'); // Default to dark premium theme

 useEffect(() => {
 // Read stored preference
 const storedTheme = localStorage.getItem('theme') as Theme | null;
 if (storedTheme) {
 setTheme(storedTheme);
 document.documentElement.className = storedTheme;
 } else {
 document.documentElement.className = 'dark';
 }
 }, []);

 const toggleTheme = () => {
 const newTheme = theme === 'dark' ? 'light' : 'dark';
 setTheme(newTheme);
 localStorage.setItem('theme', newTheme);
 document.documentElement.className = newTheme;
 };

 return (
 <ThemeContext.Provider value={{ theme, toggleTheme }}>
 {children}
 </ThemeContext.Provider>
 );
}

export function useTheme() {
 const context = useContext(ThemeContext);
 if (context === undefined) {
 throw new Error('useTheme must be used within a ThemeProvider');
 }
 return context;
}
