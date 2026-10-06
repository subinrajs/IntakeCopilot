-- A reused step may point at a row that is later deleted (the nightly demo reset removes live
-- cases, whose steps eval runs may have reused). Keep the copy and drop the pointer.
ALTER TABLE pipeline_steps DROP CONSTRAINT pipeline_steps_reused_from_fkey;
ALTER TABLE pipeline_steps ADD CONSTRAINT pipeline_steps_reused_from_fkey
  FOREIGN KEY (reused_from) REFERENCES pipeline_steps ON DELETE SET NULL;
