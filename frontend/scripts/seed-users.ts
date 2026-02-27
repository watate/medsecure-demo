/**
 * Seed script for Better Auth users.
 *
 * Reads SEED_EMAIL, SEED_PASSWORD, and SEED_NAME from the .env file
 * (or from environment variables if set inline).
 *
 * Usage:
 *   # Set SEED_EMAIL and SEED_PASSWORD in .env, then:
 *   npm run seed
 *
 *   # Or pass inline:
 *   SEED_EMAIL=user@example.com SEED_PASSWORD=changeme npm run seed
 *
 * This will:
 *   1. Create seed users in the auth.db SQLite database
 *
 * Required variables (in .env or environment):
 *   SEED_EMAIL    - email for the seed user
 *   SEED_PASSWORD - password for the seed user
 *   SEED_NAME     - display name (optional, defaults to "Admin")
 */

import "dotenv/config";
import { auth } from "../lib/auth";

interface SeedUser {
  email: string;
  password: string;
  name: string;
}

const email = process.env.SEED_EMAIL;
const password = process.env.SEED_PASSWORD;

if (!email || !password) {
  console.error("Error: SEED_EMAIL and SEED_PASSWORD are required.");
  console.error("Set them in .env or pass inline: SEED_EMAIL=user@example.com SEED_PASSWORD=changeme npm run seed");
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
