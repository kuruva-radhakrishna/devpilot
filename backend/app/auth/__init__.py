"""Email/password authentication for DevPilot.

A small, self-contained auth layer: bcrypt password hashing, stateless JWT
sessions, and a Postgres-backed user store. Only active when a DATABASE_URL is
configured (see config.auth_available); without one the app runs open.
"""
