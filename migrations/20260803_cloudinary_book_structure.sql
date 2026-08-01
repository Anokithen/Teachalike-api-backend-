-- Persist a canonical Cloudinary asset root without moving existing files.
-- Safe to run repeatedly on MySQL. Run database_setup afterwards to backfill
-- roots from saved book/teacher records using the application's sanitizer.
SET @add_book_asset_root = IF(
  EXISTS(
    SELECT 1 FROM information_schema.columns
    WHERE table_schema=DATABASE() AND table_name='books'
      AND column_name='asset_root_folder'
  ),
  'SELECT 1',
  'ALTER TABLE books ADD COLUMN asset_root_folder VARCHAR(500) NULL'
);
PREPARE add_book_asset_root_stmt FROM @add_book_asset_root;
EXECUTE add_book_asset_root_stmt;
DEALLOCATE PREPARE add_book_asset_root_stmt;
