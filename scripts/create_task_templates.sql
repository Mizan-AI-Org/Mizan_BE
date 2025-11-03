-- Minimal creation of scheduling.task_templates required for migrations to proceed
CREATE TABLE IF NOT EXISTS task_templates (
  id uuid NOT NULL PRIMARY KEY,
  name varchar(255) NOT NULL,
  description text NULL,
  template_type varchar(20) NOT NULL,
  is_active boolean NOT NULL,
  created_at timestamp with time zone NOT NULL,
  updated_at timestamp with time zone NOT NULL,
  tasks jsonb NOT NULL,
  frequency varchar(20) NOT NULL,
  ai_generated boolean NOT NULL,
  ai_prompt text NULL,
  created_by_id uuid NULL,
  restaurant_id uuid NOT NULL
);

-- Indexes from scheduling.0007
CREATE INDEX IF NOT EXISTS task_templa_restaur_a02412_idx ON task_templates (restaurant_id, template_type);
CREATE INDEX IF NOT EXISTS task_templa_frequen_a0e85e_idx ON task_templates (frequency);