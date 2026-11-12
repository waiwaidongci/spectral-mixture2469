-- New databases bootstrap metadata; apply reviewed migrations for later upgrades.
CREATE INDEX endmember_library_idx ON endmembers (library_id);
CREATE INDEX note_run_status_idx ON replication_notes (run_id,status);
