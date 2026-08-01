-- Teacher-authored books. Safe to run repeatedly on MySQL.
DELIMITER $$
CREATE PROCEDURE migrate_teacher_book_ownership()
BEGIN
  DECLARE asset_owner_fk VARCHAR(64) DEFAULT NULL;
  DECLARE asset_owner_delete_rule VARCHAR(20) DEFAULT NULL;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='books' AND column_name='description') THEN
    ALTER TABLE books ADD COLUMN description TEXT NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='books' AND column_name='created_by_account_id') THEN
    ALTER TABLE books ADD COLUMN created_by_account_id INT NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='books' AND column_name='creator_name_snapshot') THEN
    ALTER TABLE books ADD COLUMN creator_name_snapshot VARCHAR(120) NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='books' AND column_name='creation_request_id') THEN
    ALTER TABLE books ADD COLUMN creation_request_id VARCHAR(64) NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_schema=DATABASE() AND table_name='books' AND column_name='updated_at') THEN
    ALTER TABLE books ADD COLUMN updated_at DATETIME NULL;
  END IF;

  UPDATE books SET updated_at = created_at WHERE updated_at IS NULL;
  ALTER TABLE books MODIFY updated_at DATETIME NOT NULL
    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP;

  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='books' AND index_name='ix_books_created_by_account_id') THEN
    CREATE INDEX ix_books_created_by_account_id ON books (created_by_account_id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.statistics WHERE table_schema=DATABASE() AND table_name='books' AND index_name='uq_books_creator_request') THEN
    CREATE UNIQUE INDEX uq_books_creator_request ON books (created_by_account_id, creation_request_id);
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.key_column_usage
    WHERE table_schema=DATABASE() AND table_name='books'
      AND column_name='created_by_account_id'
      AND referenced_table_name='parents' AND referenced_column_name='id'
  ) THEN
    ALTER TABLE books ADD CONSTRAINT fk_books_created_by_account
      FOREIGN KEY (created_by_account_id) REFERENCES parents(id) ON DELETE SET NULL;
  END IF;

  -- Book media must outlive a deleted teacher. Ordinary account asset ledger
  -- rows are explicitly removed by account cleanup before the account delete.
  SELECT kcu.constraint_name, rc.delete_rule
    INTO asset_owner_fk, asset_owner_delete_rule
  FROM information_schema.key_column_usage kcu
  JOIN information_schema.referential_constraints rc
    ON rc.constraint_schema = kcu.constraint_schema
   AND rc.constraint_name = kcu.constraint_name
   AND rc.table_name = kcu.table_name
  WHERE kcu.table_schema=DATABASE() AND kcu.table_name='assets'
    AND kcu.column_name='owner_user_id'
    AND kcu.referenced_table_name='parents'
  LIMIT 1;

  IF asset_owner_fk IS NOT NULL AND asset_owner_delete_rule <> 'SET NULL' THEN
    SET @drop_asset_owner_fk = CONCAT(
      'ALTER TABLE assets DROP FOREIGN KEY `',
      REPLACE(asset_owner_fk, '`', '``'), '`'
    );
    PREPARE drop_asset_owner_fk_stmt FROM @drop_asset_owner_fk;
    EXECUTE drop_asset_owner_fk_stmt;
    DEALLOCATE PREPARE drop_asset_owner_fk_stmt;
    SET asset_owner_fk = NULL;
  END IF;
  ALTER TABLE assets MODIFY owner_user_id INT NULL;
  IF asset_owner_fk IS NULL THEN
    ALTER TABLE assets ADD CONSTRAINT fk_assets_owner
      FOREIGN KEY (owner_user_id) REFERENCES parents(id) ON DELETE SET NULL;
  END IF;
END$$
DELIMITER ;
CALL migrate_teacher_book_ownership();
DROP PROCEDURE migrate_teacher_book_ownership;

-- Existing books retain NULL ownership and serialize as "Created by TeachAlike".
