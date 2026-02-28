import aiosqlite

from app.config import settings

DB_PATH = settings.database_path


async def get_db() -> aiosqlite.Connection:
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db


async def init_db() -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS scans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS scan_branches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL REFERENCES scans(id),
                branch TEXT NOT NULL,
                tool TEXT NOT NULL,
                total INTEGER NOT NULL DEFAULT 0,
                open INTEGER NOT NULL DEFAULT 0,
                fixed INTEGER NOT NULL DEFAULT 0,
                dismissed INTEGER NOT NULL DEFAULT 0,
                critical INTEGER NOT NULL DEFAULT 0,
                high INTEGER NOT NULL DEFAULT 0,
                medium INTEGER NOT NULL DEFAULT 0,
                low INTEGER NOT NULL DEFAULT 0,
                other INTEGER NOT NULL DEFAULT 0,
                estimated_prompt_tokens INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL REFERENCES scans(id),
                branch TEXT NOT NULL,
                alert_number INTEGER NOT NULL,
                rule_id TEXT NOT NULL,
                rule_description TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL,
                tool TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                start_line INTEGER NOT NULL DEFAULT 0,
                end_line INTEGER NOT NULL DEFAULT 0,
                message TEXT NOT NULL DEFAULT '',
                html_url TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                dismissed_at TEXT,
                fixed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS devin_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                alert_number INTEGER NOT NULL,
                rule_id TEXT NOT NULL,
                file_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'running',
                pr_url TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS replay_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo TEXT NOT NULL,
                scan_id INTEGER REFERENCES scans(id),
                started_at TEXT NOT NULL DEFAULT (datetime('now')),
                ended_at TEXT,
                status TEXT NOT NULL DEFAULT 'running',
                tools TEXT NOT NULL DEFAULT '[]',
                branch_name TEXT
            );

            CREATE TABLE IF NOT EXISTS replay_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES replay_runs(id),
                tool TEXT NOT NULL,
                event_type TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                alert_number INTEGER,
                timestamp_offset_ms INTEGER NOT NULL DEFAULT 0,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS generated_reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL REFERENCES scans(id),
                report_type TEXT NOT NULL,
                report_data TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS api_remediation_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tool TEXT NOT NULL,
                alert_number INTEGER NOT NULL,
                rule_id TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                commit_sha TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS copilot_autofix_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_number INTEGER NOT NULL,
                rule_id TEXT NOT NULL DEFAULT '',
                file_path TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                autofix_status TEXT,
                commit_sha TEXT,
                description TEXT,
                error_message TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_alerts_scan_branch ON alerts(scan_id, branch);
            CREATE INDEX IF NOT EXISTS idx_scan_branches_scan ON scan_branches(scan_id);
            CREATE INDEX IF NOT EXISTS idx_devin_sessions_status ON devin_sessions(status);
            CREATE INDEX IF NOT EXISTS idx_devin_sessions_session_alert
                ON devin_sessions(session_id, alert_number);
            CREATE INDEX IF NOT EXISTS idx_replay_events_run ON replay_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_generated_reports_scan ON generated_reports(scan_id, report_type);
            CREATE INDEX IF NOT EXISTS idx_api_remediation_jobs_tool ON api_remediation_jobs(tool, status);
            CREATE INDEX IF NOT EXISTS idx_copilot_autofix_jobs_alert
                ON copilot_autofix_jobs(alert_number);
            """
        )

        # Lightweight migrations for existing local DBs
        cursor = await db.execute("PRAGMA table_info(scan_branches)")
        columns = {row[1] for row in await cursor.fetchall()}
        if "estimated_prompt_tokens" not in columns:
            await db.execute(
                "ALTER TABLE scan_branches ADD COLUMN estimated_prompt_tokens INTEGER NOT NULL DEFAULT 0"
            )

        cursor = await db.execute("PRAGMA table_info(replay_events)")
        re_columns = {row[1] for row in await cursor.fetchall()}
        if "metadata" not in re_columns:
            await db.execute(
                "ALTER TABLE replay_events ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
            )

        cursor = await db.execute("PRAGMA table_info(replay_runs)")
        rr_columns = {row[1] for row in await cursor.fetchall()}
        if "branch_name" not in rr_columns:
            await db.execute(
                "ALTER TABLE replay_runs ADD COLUMN branch_name TEXT"
            )

        # Migrate devin_sessions: remove UNIQUE constraint on session_id
        # so grouped sessions (multiple alerts per session) can share one id.
        cursor = await db.execute("PRAGMA index_list(devin_sessions)")
        index_rows = await cursor.fetchall()
        has_unique_idx = any(
            row[1] == "sqlite_autoindex_devin_sessions_1"
            for row in index_rows
        )
        if has_unique_idx:
            await db.executescript(
                """
                CREATE TABLE IF NOT EXISTS devin_sessions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    alert_number INTEGER NOT NULL,
                    rule_id TEXT NOT NULL,
                    file_path TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'running',
                    pr_url TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );
                INSERT INTO devin_sessions_new
                    SELECT * FROM devin_sessions;
                DROP TABLE devin_sessions;
                ALTER TABLE devin_sessions_new
                    RENAME TO devin_sessions;
                CREATE INDEX IF NOT EXISTS idx_devin_sessions_status
                    ON devin_sessions(status);
                CREATE INDEX IF NOT EXISTS idx_devin_sessions_session_alert
                    ON devin_sessions(session_id, alert_number);
                """
            )

        await db.commit()
