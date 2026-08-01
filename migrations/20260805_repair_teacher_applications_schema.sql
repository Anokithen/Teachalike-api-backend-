-- Additive, repeatable MySQL repair for teacher application storage.
-- This migration never drops, truncates, recreates, or deletes production data.

SET @repair_sql = IF(
    EXISTS(
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles'
    ) AND NOT EXISTS(
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = 'teacher_applications'
    ),
    'RENAME TABLE teacher_profiles TO teacher_applications',
    'SELECT 1'
);
PREPARE repair_stmt FROM @repair_sql;
EXECUTE repair_stmt;
DEALLOCATE PREPARE repair_stmt;

CREATE TABLE IF NOT EXISTS teacher_applications (
    id INTEGER NOT NULL AUTO_INCREMENT,
    account_id INTEGER NOT NULL,
    phone_number VARCHAR(40) NULL,
    address VARCHAR(500) NULL,
    teacher_type VARCHAR(30) NULL,
    school_name VARCHAR(200) NULL,
    tuition_name VARCHAR(200) NULL,
    approval_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    reviewed_by_id INTEGER NULL,
    reviewed_at DATETIME NULL,
    rejection_reason VARCHAR(1000) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT uq_teacher_applications_account_id UNIQUE (account_id),
    CONSTRAINT fk_teacher_applications_account FOREIGN KEY (account_id)
        REFERENCES parents (id) ON DELETE CASCADE,
    CONSTRAINT fk_teacher_applications_reviewer FOREIGN KEY (reviewed_by_id)
        REFERENCES parents (id) ON DELETE SET NULL,
    INDEX ix_teacher_applications_approval_status (approval_status),
    INDEX ix_teacher_applications_reviewed_by_id (reviewed_by_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Add each missing column independently so partially applied deployments heal.
SET @repair_sql = IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'id'),
    'SELECT 1',
    'ALTER TABLE teacher_applications ADD COLUMN id INTEGER NULL FIRST'
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

SET @repair_sql = IF(
    EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'account_id'),
    'SELECT 1',
    'ALTER TABLE teacher_applications ADD COLUMN account_id INTEGER NULL AFTER id'
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'phone_number'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN phone_number VARCHAR(40) NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'address'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN address VARCHAR(500) NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'teacher_type'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN teacher_type VARCHAR(30) NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'school_name'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN school_name VARCHAR(200) NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'tuition_name'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN tuition_name VARCHAR(200) NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'approval_status'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN approval_status VARCHAR(20) NULL DEFAULT ''pending''');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'reviewed_by_id'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN reviewed_by_id INTEGER NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'reviewed_at'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN reviewed_at DATETIME NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'rejection_reason'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN rejection_reason VARCHAR(1000) NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'created_at'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN created_at DATETIME NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'updated_at'), 'SELECT 1', 'ALTER TABLE teacher_applications ADD COLUMN updated_at DATETIME NULL');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

-- Recover rows when an interrupted deployment left both table names present.
SET @legacy_phone_number = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'phone_number'), 'old.phone_number', 'NULL');
SET @legacy_address = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'address'), 'old.address', 'NULL');
SET @legacy_teacher_type = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'teacher_type'), 'old.teacher_type', 'NULL');
SET @legacy_school_name = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'school_name'), 'old.school_name', 'NULL');
SET @legacy_tuition_name = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'tuition_name'), 'old.tuition_name', 'NULL');
SET @legacy_approval_status = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'approval_status'), 'COALESCE(old.approval_status, ''approved'')', '''approved''');
SET @legacy_reviewed_by_id = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'reviewed_by_id'), 'old.reviewed_by_id', 'NULL');
SET @legacy_reviewed_at = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'reviewed_at'), 'old.reviewed_at', 'NULL');
SET @legacy_rejection_reason = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'rejection_reason'), 'old.rejection_reason', 'NULL');
SET @legacy_created_at = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'created_at'), 'COALESCE(old.created_at, CURRENT_TIMESTAMP)', 'CURRENT_TIMESTAMP');
SET @legacy_updated_at = IF(EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'updated_at'), 'COALESCE(old.updated_at, CURRENT_TIMESTAMP)', 'CURRENT_TIMESTAMP');
SET @repair_sql = IF(
    EXISTS(SELECT 1 FROM information_schema.tables WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles')
    AND EXISTS(SELECT 1 FROM information_schema.columns WHERE table_schema = DATABASE() AND table_name = 'teacher_profiles' AND column_name = 'account_id'),
    CONCAT(
        'INSERT INTO teacher_applications (account_id, phone_number, address, teacher_type, school_name, tuition_name, approval_status, reviewed_by_id, reviewed_at, rejection_reason, created_at, updated_at) SELECT old.account_id, ',
        @legacy_phone_number, ', ', @legacy_address, ', ', @legacy_teacher_type,
        ', ', @legacy_school_name, ', ', @legacy_tuition_name, ', ',
        @legacy_approval_status, ', ', @legacy_reviewed_by_id, ', ',
        @legacy_reviewed_at, ', ', @legacy_rejection_reason, ', ',
        @legacy_created_at, ', ', @legacy_updated_at,
        ' FROM teacher_profiles old WHERE NOT EXISTS (SELECT 1 FROM teacher_applications existing WHERE existing.account_id = old.account_id)'
    ),
    'SELECT 1'
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

UPDATE teacher_applications SET approval_status = 'pending' WHERE approval_status IS NULL;
UPDATE teacher_applications SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL;
UPDATE teacher_applications SET updated_at = CURRENT_TIMESTAMP WHERE updated_at IS NULL;
SET @next_teacher_application_id = (SELECT COALESCE(MAX(id), 0) FROM teacher_applications);
UPDATE teacher_applications
SET id = (@next_teacher_application_id := @next_teacher_application_id + 1)
WHERE id IS NULL
ORDER BY account_id;

-- These diagnostic queries print clear deployment evidence before a following
-- NOT NULL or UNIQUE operation fails. No conflicting row is removed.
SELECT CASE
    WHEN EXISTS(SELECT 1 FROM teacher_applications WHERE account_id IS NULL)
    THEN 'ERROR: teacher_applications contains rows without account_id; manual review required.'
    ELSE 'teacher_applications account ownership: OK'
END AS teacher_application_repair_status;
SELECT CASE
    WHEN EXISTS(SELECT 1 FROM teacher_applications GROUP BY account_id HAVING COUNT(*) > 1)
    THEN 'ERROR: duplicate teacher application rows exist; unique constraint cannot be added safely.'
    ELSE 'teacher_applications account uniqueness: OK'
END AS teacher_application_repair_status;

SET @repair_sql = IF(
    EXISTS(
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = 'teacher_applications'
        GROUP BY index_name, non_unique
        HAVING non_unique = 0 AND COUNT(*) = 1 AND MAX(column_name = 'account_id') = 1
    ),
    'SELECT 1',
    'CREATE UNIQUE INDEX uq_teacher_applications_account_repair ON teacher_applications (account_id)'
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

SET @repair_sql = IF(
    EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'id' AND non_unique = 0),
    'SELECT 1',
    IF(
        NOT EXISTS(SELECT 1 FROM information_schema.table_constraints WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND constraint_type = 'PRIMARY KEY'),
        'ALTER TABLE teacher_applications ADD PRIMARY KEY (id)',
        'CREATE UNIQUE INDEX uq_teacher_applications_id_repair ON teacher_applications (id)'
    )
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'approval_status'), 'SELECT 1', 'CREATE INDEX ix_teacher_applications_approval_repair ON teacher_applications (approval_status)');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(EXISTS(SELECT 1 FROM information_schema.statistics WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'reviewed_by_id'), 'SELECT 1', 'CREATE INDEX ix_teacher_applications_reviewer_repair ON teacher_applications (reviewed_by_id)');
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

ALTER TABLE teacher_applications
    MODIFY COLUMN id INTEGER NOT NULL AUTO_INCREMENT,
    MODIFY COLUMN account_id INTEGER NOT NULL,
    MODIFY COLUMN approval_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    MODIFY COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    MODIFY COLUMN updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

SET @repair_sql = IF(
    EXISTS(SELECT 1 FROM information_schema.key_column_usage WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'account_id' AND referenced_table_name = 'parents'),
    'SELECT 1',
    'ALTER TABLE teacher_applications ADD CONSTRAINT fk_teacher_applications_account_repair FOREIGN KEY (account_id) REFERENCES parents(id) ON DELETE CASCADE'
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;
SET @repair_sql = IF(
    EXISTS(SELECT 1 FROM information_schema.key_column_usage WHERE table_schema = DATABASE() AND table_name = 'teacher_applications' AND column_name = 'reviewed_by_id' AND referenced_table_name = 'parents'),
    'SELECT 1',
    'ALTER TABLE teacher_applications ADD CONSTRAINT fk_teacher_applications_reviewer_repair FOREIGN KEY (reviewed_by_id) REFERENCES parents(id) ON DELETE SET NULL'
);
PREPARE repair_stmt FROM @repair_sql; EXECUTE repair_stmt; DEALLOCATE PREPARE repair_stmt;

-- Legacy teacher accounts receive approved application rows. Existing rows,
-- including pending and rejected decisions, are excluded and never overwritten.
INSERT INTO teacher_applications
    (account_id, approval_status, created_at, updated_at)
SELECT p.id, 'approved', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
FROM parents p
LEFT JOIN teacher_applications ta ON ta.account_id = p.id
WHERE p.role = 'teacher' AND ta.account_id IS NULL;
