import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SemanticOS — Enterprise Log Intelligence",
  description: "Advanced semantic log clustering and noise reduction",
};

import { AuthProvider } from "@/context/AuthContext";
import { ThemeProvider } from "next-themes";
import { ToastProvider } from "@/context/ToastContext";

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      suppressHydrationWarning
      className="h-full antialiased"
    >
      <body className="min-h-full flex flex-col">
        <ThemeProvider attribute="class" defaultTheme="dark" enableSystem>
          <ToastProvider>
            <AuthProvider>
              {children}
            </AuthProvider>
          </ToastProvider>
        </ThemeProvider>
      </body>
    </html>
  );
}
