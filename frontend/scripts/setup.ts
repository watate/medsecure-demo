/**
 * One-command auth setup: runs better-auth migrations then seeds users.
 *
 * Usage:
 *   npm run setup
 *
 * Requires SEED_EMAIL and SEED_PASSWORD in .env (or as env vars).
 */

import { execSync } from "child_process";
import path from "path";

const frontendDir = path.resolve(__dirname, "..");

console.log("Running better-auth migrations...");
execSync("npx @better-auth/cli migrate --yes", {
  cwd: frontendDir,
  stdio: "inherit",
});

console.log("\nSeeding users...");
execSync("npx tsx scripts/seed-users.ts", {
  cwd: frontendDir,
  stdio: "inherit",
});

console.log("\nSetup complete!");
