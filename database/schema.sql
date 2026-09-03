CREATE TABLE accounts (
    account_code VARCHAR(10) PRIMARY KEY,
    account_name TEXT NOT NULL,
    account_type TEXT NOT NULL CHECK (
        account_type IN ('asset', 'liability', 'equity', 'revenue', 'expense')
    ),
    normal_balance TEXT NOT NULL CHECK (
        normal_balance IN ('debit', 'credit')
    )
);

CREATE TABLE journal_entries (
    journal_id TEXT PRIMARY KEY,
    journal_date DATE NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE journal_lines (
    journal_line_id BIGSERIAL PRIMARY KEY,
    journal_id TEXT NOT NULL REFERENCES journal_entries(journal_id),
    line_number INTEGER NOT NULL,
    account_code VARCHAR(10) NOT NULL REFERENCES accounts(account_code),
    description TEXT NOT NULL,
    debit NUMERIC(14, 2) NOT NULL DEFAULT 0,
    credit NUMERIC(14, 2) NOT NULL DEFAULT 0,
    external_reference TEXT,
    CONSTRAINT journal_line_amount_check CHECK (
        (debit > 0 AND credit = 0)
        OR (credit > 0 AND debit = 0)
    ),
    CONSTRAINT journal_line_unique_position UNIQUE (journal_id, line_number)
);

CREATE TABLE bank_transactions (
    bank_transaction_id TEXT PRIMARY KEY,
    transaction_date DATE NOT NULL,
    description TEXT NOT NULL,
    amount NUMERIC(14, 2) NOT NULL,
    external_reference TEXT,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE close_exceptions (
    exception_id TEXT PRIMARY KEY,
    exception_type TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    status TEXT NOT NULL DEFAULT 'open' CHECK (
        status IN ('open', 'reviewed', 'resolved', 'dismissed')
    ),
    source_ids TEXT[] NOT NULL,
    evidence JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE exception_decisions (
    decision_id BIGSERIAL PRIMARY KEY,
    exception_id TEXT NOT NULL REFERENCES close_exceptions(exception_id),
    decision TEXT NOT NULL CHECK (
        decision IN ('acknowledge', 'resolve', 'dismiss')
    ),
    reviewer TEXT NOT NULL,
    note TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX journal_lines_external_reference_idx
    ON journal_lines(external_reference);

CREATE INDEX bank_transactions_external_reference_idx
    ON bank_transactions(external_reference);

CREATE INDEX close_exceptions_status_severity_idx
    ON close_exceptions(status, severity);