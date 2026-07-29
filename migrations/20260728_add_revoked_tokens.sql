-- Persistent access/refresh token revocation. Apply before deploying the
-- database-backed logout flow to an existing MySQL database.
CREATE TABLE revoked_tokens (
    id INTEGER NOT NULL AUTO_INCREMENT,
    jti VARCHAR(64) NOT NULL,
    token_type VARCHAR(16) NOT NULL,
    expires_at DATETIME NOT NULL,
    revoked_at DATETIME NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT uq_revoked_tokens_jti UNIQUE (jti),
    INDEX ix_revoked_tokens_expires_at (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
