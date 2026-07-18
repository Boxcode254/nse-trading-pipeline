-- Decision Journal Schema for Paper Trading
-- Location: ~/.trading/learning/decisions.db

-- 1. decisions - every paper trade decision logged
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,                    -- ISO8601
    symbol TEXT NOT NULL,                       -- NSE symbol, e.g. RELIANCE, TCS
    signal_source TEXT NOT NULL,                -- strategy name: 'momentum', 'mean_reversion', 'breakout', etc.
    signal_strength INTEGER NOT NULL,           -- 0-100
    direction TEXT NOT NULL CHECK (direction IN ('LONG', 'SHORT')),
    entry_price REAL NOT NULL,
    position_size INTEGER NOT NULL,             -- shares
    stop_loss REAL,                             -- nullable
    take_profit REAL,                           -- nullable
    confidence INTEGER NOT NULL,                -- 0-100
    reasoning TEXT,                             -- why this trade
    rule_version INTEGER NOT NULL,              -- which rule set was active
    status TEXT NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN', 'CLOSED', 'EXPIRED')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Indexes for decisions
CREATE INDEX IF NOT EXISTS idx_decisions_symbol ON decisions(symbol);
CREATE INDEX IF NOT EXISTS idx_decisions_timestamp ON decisions(timestamp);
CREATE INDEX IF NOT EXISTS idx_decisions_status ON decisions(status);
CREATE INDEX IF NOT EXISTS idx_decisions_rule_version ON decisions(rule_version);
CREATE INDEX IF NOT EXISTS idx_decisions_signal_source ON decisions(signal_source);

-- 2. outcomes - result after trade closes
CREATE TABLE IF NOT EXISTS outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    decision_id INTEGER NOT NULL,               -- FK to decisions.id
    exit_timestamp TEXT NOT NULL,
    exit_price REAL NOT NULL,
    pnl_absolute REAL NOT NULL,
    pnl_pct REAL NOT NULL,
    hold_duration_minutes INTEGER NOT NULL,
    exit_reason TEXT NOT NULL CHECK (exit_reason IN ('STOP_LOSS', 'TAKE_PROFIT', 'TIME_EXPIRY', 'MANUAL')),
    market_outcome TEXT NOT NULL CHECK (market_outcome IN ('UP', 'DOWN', 'SIDEWAYS')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (decision_id) REFERENCES decisions(id) ON DELETE CASCADE
);

-- Indexes for outcomes
CREATE INDEX IF NOT EXISTS idx_outcomes_decision_id ON outcomes(decision_id);
CREATE INDEX IF NOT EXISTS idx_outcomes_exit_timestamp ON outcomes(exit_timestamp);
CREATE INDEX IF NOT EXISTS idx_outcomes_exit_reason ON outcomes(exit_reason);

-- 3. rule_versions - track rule evolution
CREATE TABLE IF NOT EXISTS rule_versions (
    version INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    description TEXT NOT NULL,                  -- what changed
    params_json TEXT NOT NULL,                  -- JSON blob of all filter weights/thresholds
    parent_version INTEGER,                     -- nullable, for lineage
    FOREIGN KEY (parent_version) REFERENCES rule_versions(version) ON DELETE SET NULL
);

-- Index for rule_versions
CREATE INDEX IF NOT EXISTS idx_rule_versions_parent ON rule_versions(parent_version);