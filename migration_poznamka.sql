-- Migrace: pridani sloupce poznamka do tabulky kojeni
-- Spusteni:
--   sqlite3 ~/dite.db < migration_poznamka.sql
-- nebo s vlastni cestou k databazi:
--   sqlite3 /cesta/k/dite.db < migration_poznamka.sql

ALTER TABLE kojeni ADD COLUMN poznamka TEXT;
