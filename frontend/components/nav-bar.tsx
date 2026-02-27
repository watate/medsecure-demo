"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useSession, signOut } from "@/lib/auth-client";
import { Button } from "@/components/ui/button";

export function NavBar() {
  const router = useRouter();
  const pathname = usePathname();
  const { data: session } = useSession();

  const handleSignOut = async () => {
    await signOut();
    router.push("/login");
  };

  // Hide nav bar on login page
  if (pathname === "/login") {
    return null;
  }

  return (
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
          {session?.user && (
            <>
              <span className="text-xs text-muted-foreground">
                {session.user.email}
              </span>
              <Button variant="outline" size="sm" onClick={handleSignOut}>
                Sign out
              </Button>
            </>
          )}
        </div>
      </div>
    </nav>
  );
}
