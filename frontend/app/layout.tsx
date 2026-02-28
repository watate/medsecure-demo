import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { NavBar } from "@/components/nav-bar";
import { RepoProvider } from "@/lib/repo-context";

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
        <RepoProvider>
          <NavBar />
          <main className="mx-auto max-w-7xl px-6 py-8">{children}</main>
        </RepoProvider>
      </body>
    </html>
  );
}
