import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

// Self-hosted (Geist, SIL OFL 1.1 — see fonts/NOTICE.md). Loading from
// Google at build time broke every air-gapped on-prem build. Variable fonts,
// latin subset: the same files next/font/google served.
const geistSans = localFont({
 src: "./fonts/Geist-Variable.woff2",
 weight: "100 900",
 display: "swap",
 // Keep the old variable name too: existing styles reference --font-noto-sans.
 variable: "--font-noto-sans",
});

const geistMono = localFont({
 src: "./fonts/GeistMono-Variable.woff2",
 weight: "100 900",
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
