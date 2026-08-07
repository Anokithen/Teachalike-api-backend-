-- Feature 7: Google authentication, email verification, and approval mail.
-- MySQL-compatible, idempotent migration for Railway pre-deploy use.

DELIMITER //

DROP PROCEDURE IF EXISTS teachalike_add_column_if_missing//
CREATE PROCEDURE teachalike_add_column_if_missing(
  IN table_name_in VARCHAR(64),
  IN column_name_in VARCHAR(64),
  IN ddl_in TEXT
)
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = table_name_in
      AND column_name = column_name_in
  ) THEN
    SET @stmt = CONCAT('ALTER TABLE `', table_name_in, '` ADD COLUMN ', ddl_in);
    PREPARE prepared_stmt FROM @stmt;
    EXECUTE prepared_stmt;
    DEALLOCATE PREPARE prepared_stmt;
  END IF;
END//

DROP PROCEDURE IF EXISTS teachalike_add_index_if_missing//
CREATE PROCEDURE teachalike_add_index_if_missing(
  IN table_name_in VARCHAR(64),
  IN index_name_in VARCHAR(64),
  IN ddl_in TEXT
)
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = table_name_in
      AND index_name = index_name_in
  ) THEN
    SET @stmt = ddl_in;
    PREPARE prepared_stmt FROM @stmt;
    EXECUTE prepared_stmt;
    DEALLOCATE PREPARE prepared_stmt;
  END IF;
END//

DELIMITER ;

CALL teachalike_add_column_if_missing('parents', 'email_verified', '`email_verified` BOOLEAN NULL');
CALL teachalike_add_column_if_missing('parents', 'email_verified_at', '`email_verified_at` DATETIME NULL');
CALL teachalike_add_column_if_missing('parents', 'auth_provider', '`auth_provider` VARCHAR(30) NULL');
CALL teachalike_add_column_if_missing('parents', 'google_subject', '`google_subject` VARCHAR(255) NULL');
CALL teachalike_add_column_if_missing('parents', 'last_login_at', '`last_login_at` DATETIME NULL');

UPDATE parents SET email_verified = 1 WHERE email_verified IS NULL;
UPDATE parents SET email_verified_at = created_at WHERE email_verified = 1 AND email_verified_at IS NULL;
UPDATE parents SET auth_provider = 'password' WHERE auth_provider IS NULL;

ALTER TABLE parents
  MODIFY COLUMN email_verified BOOLEAN NOT NULL DEFAULT 1,
  MODIFY COLUMN auth_provider VARCHAR(30) NOT NULL DEFAULT 'password';

CALL teachalike_add_index_if_missing(
  'parents',
  'uq_parents_google_subject',
  'CREATE UNIQUE INDEX uq_parents_google_subject ON parents (google_subject)'
);

CALL teachalike_add_column_if_missing('teacher_applications', 'approval_version', '`approval_version` INTEGER NULL DEFAULT 0');
UPDATE teacher_applications SET approval_version = 0 WHERE approval_version IS NULL;
ALTER TABLE teacher_applications MODIFY COLUMN approval_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS account_identities (
  id INTEGER NOT NULL AUTO_INCREMENT,
  account_id INTEGER NOT NULL,
  provider VARCHAR(30) NOT NULL,
  provider_subject VARCHAR(255) NOT NULL,
  provider_email VARCHAR(120) NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_account_identity_provider_subject (provider, provider_subject),
  KEY ix_account_identities_account_id (account_id),
  CONSTRAINT fk_account_identities_account FOREIGN KEY (account_id) REFERENCES parents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS email_verification_tokens (
  id INTEGER NOT NULL AUTO_INCREMENT,
  account_id INTEGER NOT NULL,
  token_hash VARCHAR(64) NOT NULL,
  purpose VARCHAR(50) NOT NULL DEFAULT 'email_verification',
  expires_at DATETIME NOT NULL,
  used_at DATETIME NULL,
  revoked_at DATETIME NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  request_ip_hash VARCHAR(80) NULL,
  user_agent_hash VARCHAR(80) NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_email_verification_tokens_token_hash (token_hash),
  KEY ix_email_verification_tokens_account_purpose (account_id, purpose),
  CONSTRAINT fk_email_verification_tokens_account FOREIGN KEY (account_id) REFERENCES parents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS email_deliveries (
  id INTEGER NOT NULL AUTO_INCREMENT,
  recipient_account_id INTEGER NULL,
  recipient_email VARCHAR(120) NOT NULL,
  email_type VARCHAR(50) NOT NULL,
  event_key VARCHAR(190) NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  next_attempt_at DATETIME NULL,
  provider_message_id VARCHAR(255) NULL,
  last_error_code VARCHAR(80) NULL,
  context_json JSON NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  sent_at DATETIME NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_email_deliveries_event_key (event_key),
  KEY ix_email_deliveries_status_next_attempt (status, next_attempt_at),
  KEY ix_email_deliveries_recipient_account_id (recipient_account_id),
  CONSTRAINT fk_email_deliveries_recipient FOREIGN KEY (recipient_account_id) REFERENCES parents(id) ON DELETE SET NULL
);

DROP PROCEDURE IF EXISTS teachalike_add_column_if_missing;
DROP PROCEDURE IF EXISTS teachalike_add_index_if_missing;
