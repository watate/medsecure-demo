import { betterAuth } from "better-auth";
import Database from "better-sqlite3";

export const auth = betterAuth({
  database: new Database(process.env.AUTH_DB_PATH || "auth.db"),
  emailAndPassword: {
    enabled: true,
  },
});
