-- Remove the retired exit-password credential from databases where the
-- feature was previously deployed.
SET @drop_exit_password_hash = IF(
    EXISTS(
        SELECT 1
        FROM information_schema.columns
        WHERE table_schema = DATABASE()
          AND table_name = 'parents'
          AND column_name = 'exit_password_hash'
    ),
    'ALTER TABLE parents DROP COLUMN exit_password_hash',
    'SELECT 1'
);
PREPARE drop_exit_password_hash_stmt FROM @drop_exit_password_hash;
EXECUTE drop_exit_password_hash_stmt;
DEALLOCATE PREPARE drop_exit_password_hash_stmt;
