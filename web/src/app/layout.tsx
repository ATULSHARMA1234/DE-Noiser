import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";

const geistSans = Geist({
 subsets: ["latin"],
 display: "swap",
 // Keep the old variable name too: existing styles reference --font-noto-sans.
 variable: "--font-noto-sans",
});

const geistMono = Geist_Mono({
 subsets: ["latin"],
 display: "swap",
 variable: "--font-geist-mono",
});

export const metadata: Metadata = {
 title: "SemanticOS — Enterprise Log Intelligence",
 description: "Advanced semantic log clustering and noise reduction",
};

import { AuthProvider } from "@/context/AuthContext";
import { ThemeProvider } from "next-themes";
import { ToastProvider } from "@/context/ToastContext";
import { TaskProvider } from "@/context/TaskContext";

export default function RootLayout({
 children,
}: Readonly<{
 children: React.ReactNode;
}>) {
 return (
 <html
 lang="en"
 suppressHydrationWarning
 className={`h-full antialiased ${geistSans.variable} ${geistMono.variable}`}
 >
 <body className={`min-h-full flex flex-col ${geistSans.className}`}>
 <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
 <ToastProvider>
 <TaskProvider>
 <AuthProvider>
 {children}
 </AuthProvider>
 </TaskProvider>
 </ToastProvider>
 </ThemeProvider>
 </body>
 </html>
 );
}
