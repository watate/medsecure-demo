"use client";

import Image from "next/image";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Field, FieldGroup, FieldLabel } from "@/components/ui/field";
import { Input } from "@/components/ui/input";
import { signIn } from "@/lib/auth-client";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const handleSubmit: React.FormHTMLAttributes<HTMLFormElement>["onSubmit"] =
    async (e) => {
      e.preventDefault();
      setError("");
      setLoading(true);

      const { error } = await signIn.email({
        email,
        password,
        callbackURL: "/",
      });

      if (error) {
        setError(error.message ?? "Sign in failed. Please try again.");
        setLoading(false);
      } else {
        router.push("/");
      }
    };

  return (
    <div className="flex min-h-svh w-full items-center justify-center p-6 md:p-10">
      <div className="w-full max-w-sm">
        <div className="flex flex-col gap-6">
          <div className="flex flex-col items-center gap-2">
            <Image
              src="/logo.png"
              alt="MedSecure"
              width={48}
              height={48}
              className="rounded-md"
            />
            <span className="text-xl font-semibold tracking-tight">
              MedSecure
            </span>
          </div>
          <Card>
            <CardHeader>
              <CardTitle>Sign In</CardTitle>
              <CardDescription>
                Sign in to access the Cognition CodeQL remediation demo
              </CardDescription>
            </CardHeader>
            <CardContent>
              <form onSubmit={handleSubmit}>
                <FieldGroup>
                  <Field>
                    <FieldLabel htmlFor="email">Email</FieldLabel>
                    <Input
                      id="email"
                      type="email"
                      placeholder="name@company.com"
                      required
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                    />
                  </Field>
                  <Field>
                    <FieldLabel htmlFor="password">Password</FieldLabel>
                    <Input
                      id="password"
                      type="password"
                      required
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                    />
                  </Field>
                  {error && <p className="text-sm text-destructive">{error}</p>}
                  <Field>
                    <Button type="submit" disabled={loading} className="w-full">
                      {loading ? "Signing in..." : "Sign in"}
                    </Button>
                  </Field>
                </FieldGroup>
              </form>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
