import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import Image from "next/image";
import Link from "next/link";

const inter = Inter({ subsets: ["latin"], variable: "--font-sans" });

export const metadata: Metadata = {
  title: "MedSecure - CodeQL Remediation Dashboard",
  description: "Compare Devin vs Copilot Autofix vs Anthropic for CodeQL security remediation",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-background text-foreground antialiased">
        <nav className="border-b border-border bg-card">
          <div className="mx-auto flex max-w-7xl items-center justify-between px-6 py-3">
            <Link href="/" className="flex items-center gap-2">
              <Image src="/logo.png" alt="MedSecure Logo" width={32} height={32} className="rounded-md" />
              <span className="text-lg font-semibold">MedSecure</span>
            </Link>
            <div className="flex items-center gap-6">
              <Link href="/" className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
                Dashboard
              </Link>
              <Link href="/alerts" className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
                Alerts
              </Link>
              <Link href="/remediation" className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
                Remediation
              </Link>
              <Link href="/reports" className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
                Reports
              </Link>
              <Link href="/replay" className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
                Replay
              </Link>
            </div>
          </div>
        </nav>
        <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
      </body>
    </html>
  );
}
