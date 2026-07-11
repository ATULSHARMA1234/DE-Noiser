import type { Metadata } from "next";
import { Noto_Sans } from "next/font/google";
import "./globals.css";

const notoSans = Noto_Sans({
 subsets: ["latin"],
 weight: ["300", "400", "500", "600", "700"],
 display: "swap",
 variable: "--font-noto-sans",
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
 className={`h-full antialiased ${notoSans.variable}`}
 >
 <body className={`min-h-full flex flex-col ${notoSans.className}`}>
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
