-- Migrace: pridani sloupce pozice_hlavy do tabulky kojeni
-- Hodnota 0-100 (0 = leva strana L, 100 = prava strana P)
-- Spusteni:
--   sqlite3 ~/dite.db < migration_hlava.sql
-- nebo s vlastni cestou k databazi:
--   sqlite3 /cesta/k/dite.db < migration_hlava.sql

ALTER TABLE kojeni ADD COLUMN pozice_hlavy INTEGER;
