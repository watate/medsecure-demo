import { betterAuth } from "better-auth";
import Database from "better-sqlite3";

const trustedOrigins = process.env.BETTER_AUTH_URL
  ? [process.env.BETTER_AUTH_URL, "http://localhost:3000"]
  : ["http://localhost:3000"];

export const auth = betterAuth({
  database: new Database(process.env.AUTH_DB_PATH || "auth.db"),
  trustedOrigins,
  emailAndPassword: {
    enabled: true,
  },
});
