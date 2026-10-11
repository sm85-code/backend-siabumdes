"""Consume legacy unit-wide grants once; finished user sessions never resurrect."""
LEGACY_ASSIGNMENTS = """
INSERT INTO riska_assignments (user_id, session_id, assigned_by, role, unit_usaha_id, active, started_at, ended_at)
SELECT u.id, md5('legacy-riska:' || u.id), NULL, 'pengelola', u.unit_usaha_id, TRUE, CURRENT_TIMESTAMP, NULL
FROM users u JOIN ai_session_permissions p ON p.unit_usaha_id = u.unit_usaha_id
JOIN unit_usaha unit ON unit.id = u.unit_usaha_id
WHERE p.is_allowed = TRUE AND u.active = TRUE AND unit.active = TRUE
AND u.role IN ('pengelola', 'pengelola_unit', 'unit_manager', 'unit-manager', 'manager')
ON CONFLICT (user_id) DO NOTHING
"""
CONSUME_LEGACY_GRANTS = 'UPDATE ai_session_permissions SET is_allowed = FALSE WHERE is_allowed = TRUE'
