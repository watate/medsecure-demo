/**
 * Seed script for Better Auth users.
 *
 * Usage:
 *   SEED_EMAIL=user@example.com SEED_PASSWORD=changeme SEED_NAME=Admin npx tsx scripts/seed-users.ts
 *   # or via npm:
 *   SEED_EMAIL=user@example.com SEED_PASSWORD=changeme npm run seed
 *
 * This will:
 *   1. Run the Better Auth migration to create tables if needed
 *   2. Create seed users in the auth.db SQLite database
 *
 * Required environment variables:
 *   SEED_EMAIL    - email for the seed user
 *   SEED_PASSWORD - password for the seed user
 *   SEED_NAME     - display name (optional, defaults to "Admin")
 */

import { auth } from "../lib/auth";

interface SeedUser {
  email: string;
  password: string;
  name: string;
}

const email = process.env.SEED_EMAIL;
const password = process.env.SEED_PASSWORD;

if (!email || !password) {
  console.error("Error: SEED_EMAIL and SEED_PASSWORD environment variables are required.");
  console.error("Usage: SEED_EMAIL=user@example.com SEED_PASSWORD=changeme npx tsx scripts/seed-users.ts");
  process.exit(1);
}

const users: SeedUser[] = [
  {
    email,
    password,
    name: process.env.SEED_NAME || "Admin",
  },
];

async function seed() {
  console.log("Seeding users...\n");

  for (const user of users) {
    try {
      const result = await auth.api.signUpEmail({
        body: {
          email: user.email,
          password: user.password,
          name: user.name,
        },
      });

      if (result.user) {
        console.log(`  Created user: ${user.email} (${user.name})`);
      }
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : String(err);
      if (message.includes("already exists") || message.includes("UNIQUE")) {
        console.log(`  User already exists: ${user.email}`);
      } else {
        console.error(`  Failed to create ${user.email}: ${message}`);
      }
    }
  }

  console.log("\nDone!");
}

seed();
