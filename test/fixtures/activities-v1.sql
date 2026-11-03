-- Synthetic archive generated with shipped schema v1 at main da99ee4.
BEGIN TRANSACTION;
CREATE TABLE activities(id TEXT PRIMARY KEY,participant_id TEXT NOT NULL,name TEXT NOT NULL,
        category TEXT NOT NULL DEFAULT '',context TEXT NOT NULL DEFAULT '',favorite INTEGER NOT NULL DEFAULT 0 CHECK(favorite IN(0,1)),
        archived INTEGER NOT NULL DEFAULT 0 CHECK(archived IN(0,1)),version INTEGER NOT NULL DEFAULT 1,
        created_ms INTEGER NOT NULL,updated_ms INTEGER NOT NULL,origin TEXT NOT NULL,
        UNIQUE(participant_id,name),UNIQUE(participant_id,id));
INSERT INTO "activities" VALUES('27cb9f60e8233bf87f606eddb5db3e45','A','Reading renamed','','',1,0,2,1793715913000,1000000000,'legacy-writer');
INSERT INTO "activities" VALUES('26133df2f4514870dc3b57b76fa7d083','A','Work','','',0,0,1,1793715913000,1793715913000,'legacy-writer');
CREATE TABLE activity_history(activity_id TEXT NOT NULL,participant_id TEXT NOT NULL,version INTEGER NOT NULL,
        name TEXT NOT NULL,category TEXT NOT NULL,context TEXT NOT NULL,favorite INTEGER NOT NULL,archived INTEGER NOT NULL,
        changed_ms INTEGER NOT NULL,origin TEXT NOT NULL,PRIMARY KEY(activity_id,version));
INSERT INTO "activity_history" VALUES('27cb9f60e8233bf87f606eddb5db3e45','A',1,'Reading α','','',0,0,1793715913000,'legacy-writer');
INSERT INTO "activity_history" VALUES('26133df2f4514870dc3b57b76fa7d083','A',1,'Work','','',0,0,1793715913000,'legacy-writer');
INSERT INTO "activity_history" VALUES('27cb9f60e8233bf87f606eddb5db3e45','A',2,'Reading renamed','','',1,0,1000000000,'edited');
CREATE TABLE activity_label_history(event_id INTEGER PRIMARY KEY AUTOINCREMENT,label_id INTEGER NOT NULL,
        participant_id TEXT NOT NULL,activity_id TEXT,label TEXT NOT NULL,recorded_label TEXT,note TEXT NOT NULL,
        start_ms INTEGER NOT NULL,end_ms INTEGER,source TEXT NOT NULL,reviewed INTEGER NOT NULL,batch_id TEXT,
        action TEXT NOT NULL,changed_ms INTEGER NOT NULL);
INSERT INTO "activity_label_history" VALUES(1,1,'A','27cb9f60e8233bf87f606eddb5db3e45','Reading α','Reading α','Keep exact note',1000,2000,'manual',1,NULL,'created',1793715913000);
INSERT INTO "activity_label_history" VALUES(2,2,'A','26133df2f4514870dc3b57b76fa7d083','Work','Work','Suggested',3000,9000,'schedule',0,'batch-original','created',1793715913000);
INSERT INTO "activity_label_history" VALUES(3,1,'A','27cb9f60e8233bf87f606eddb5db3e45','Reading α','Reading α','Keep exact note',1000,2000,'manual',1,NULL,'before-update',1793715913000);
INSERT INTO "activity_label_history" VALUES(4,1,'A','27cb9f60e8233bf87f606eddb5db3e45','Reading renamed','Reading α','Keep exact note',1000,2000,'manual',1,NULL,'updated',1793715913000);
CREATE TABLE activity_schema(version INTEGER PRIMARY KEY,applied_ms INTEGER NOT NULL,backup_name TEXT);
INSERT INTO "activity_schema" VALUES(1,1000000000,NULL);
CREATE TABLE labels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                label TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                start_ms INTEGER NOT NULL,
                end_ms INTEGER, source TEXT NOT NULL DEFAULT 'manual', reviewed INTEGER NOT NULL DEFAULT 1, batch_id TEXT, activity_id TEXT, recorded_label TEXT,
                CHECK (end_ms IS NULL OR end_ms > start_ms)
            );
INSERT INTO "labels" VALUES(1,'A','Reading renamed','Keep exact note',1000,2000,'manual',1,NULL,'27cb9f60e8233bf87f606eddb5db3e45','Reading α');
INSERT INTO "labels" VALUES(2,'A','Work','Suggested',3000,9000,'schedule',0,'batch-original','26133df2f4514870dc3b57b76fa7d083','Work');
CREATE TABLE mobile_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                participant_id TEXT NOT NULL,
                observed_ms INTEGER NOT NULL,
                score REAL NOT NULL CHECK(score >= 0 AND score <= 100),
                state TEXT NOT NULL CHECK(state IN ('alert','engaged','relaxed','calm')),
                uncertainty_seconds INTEGER NOT NULL DEFAULT 0,
                app_version TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                created_ms INTEGER NOT NULL,
                updated_ms INTEGER NOT NULL,
                UNIQUE(participant_id, observed_ms)
            );
CREATE TABLE readings (
                participant_id TEXT NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                device_id TEXT,
                waveform_json TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                mean REAL NOT NULL,
                rms REAL NOT NULL,
                peak_to_peak REAL NOT NULL,
                PRIMARY KEY (participant_id, timestamp_ms)
            );
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX readings_time ON readings(timestamp_ms);
CREATE INDEX labels_time ON labels(participant_id, start_ms, end_ms);
CREATE INDEX activity_label_history_scope ON activity_label_history(participant_id,activity_id,event_id);
CREATE INDEX labels_activity ON labels(participant_id,activity_id);
CREATE TRIGGER mt_activity_scope_insert BEFORE INSERT ON labels WHEN NEW.activity_id IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM activities WHERE id=NEW.activity_id AND participant_id=NEW.participant_id)
        BEGIN SELECT RAISE(ABORT,'Activity belongs to another participant'); END;
CREATE TRIGGER mt_activity_scope_update BEFORE UPDATE OF activity_id,participant_id ON labels WHEN NEW.activity_id IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM activities WHERE id=NEW.activity_id AND participant_id=NEW.participant_id)
        BEGIN SELECT RAISE(ABORT,'Activity belongs to another participant'); END;
CREATE TRIGGER mt_activity_legacy_insert AFTER INSERT ON labels WHEN NEW.activity_id IS NULL BEGIN
        INSERT OR IGNORE INTO activities(id,participant_id,name,created_ms,updated_ms,origin)
        VALUES(lower(hex(randomblob(16))),NEW.participant_id,NEW.label,CAST(strftime('%s','now') AS INTEGER)*1000,CAST(strftime('%s','now') AS INTEGER)*1000,'legacy-writer');
        UPDATE labels SET activity_id=(SELECT id FROM activities WHERE participant_id=NEW.participant_id AND name=NEW.label),
            recorded_label=COALESCE(NEW.recorded_label,NEW.label) WHERE id=NEW.id; END;
CREATE TRIGGER mt_activity_legacy_update AFTER UPDATE OF label ON labels WHEN NEW.activity_id=OLD.activity_id AND
        NEW.label!=(SELECT name FROM activities WHERE id=NEW.activity_id) BEGIN
        INSERT OR IGNORE INTO activities(id,participant_id,name,created_ms,updated_ms,origin)
        VALUES(lower(hex(randomblob(16))),NEW.participant_id,NEW.label,CAST(strftime('%s','now') AS INTEGER)*1000,CAST(strftime('%s','now') AS INTEGER)*1000,'legacy-writer');
        UPDATE labels SET activity_id=(SELECT id FROM activities WHERE participant_id=NEW.participant_id AND name=NEW.label),
            recorded_label=NEW.label WHERE id=NEW.id; END;
CREATE TRIGGER mt_activity_projection_insert AFTER INSERT ON labels WHEN NEW.activity_id IS NOT NULL AND
        (NEW.label!=(SELECT name FROM activities WHERE id=NEW.activity_id) OR NEW.recorded_label IS NULL) BEGIN
        UPDATE labels SET label=(SELECT name FROM activities WHERE id=NEW.activity_id),
            recorded_label=COALESCE(NEW.recorded_label,(SELECT name FROM activities WHERE id=NEW.activity_id)) WHERE id=NEW.id; END;
CREATE TRIGGER mt_activity_projection_update AFTER UPDATE OF activity_id ON labels WHEN NEW.activity_id IS NOT NULL AND
        (NEW.label!=(SELECT name FROM activities WHERE id=NEW.activity_id) OR NEW.recorded_label IS NULL OR
         (OLD.activity_id IS NOT NULL AND NEW.activity_id!=OLD.activity_id AND NEW.recorded_label=OLD.recorded_label)) BEGIN
        UPDATE labels SET label=(SELECT name FROM activities WHERE id=NEW.activity_id),
            recorded_label=(SELECT name FROM activities WHERE id=NEW.activity_id) WHERE id=NEW.id; END;
CREATE TRIGGER mt_label_identity BEFORE UPDATE ON labels WHEN NEW.id!=OLD.id OR NEW.participant_id!=OLD.participant_id
        BEGIN SELECT RAISE(ABORT,'Label identity is immutable'); END;
CREATE TRIGGER mt_label_keep_activity BEFORE UPDATE OF activity_id ON labels WHEN OLD.activity_id IS NOT NULL AND NEW.activity_id IS NULL
        BEGIN SELECT RAISE(ABORT,'Assign a personal activity instead of clearing its identity'); END;
CREATE TRIGGER mt_activity_identity BEFORE UPDATE ON activities WHEN NEW.id!=OLD.id OR NEW.participant_id!=OLD.participant_id
        BEGIN SELECT RAISE(ABORT,'Activity identity is immutable'); END;
CREATE TRIGGER mt_activity_version BEFORE UPDATE ON activities WHEN NEW.version!=OLD.version+1
        BEGIN SELECT RAISE(ABORT,'Activity version must advance'); END;
CREATE TRIGGER mt_activity_name_projection AFTER UPDATE OF name ON activities WHEN NEW.name!=OLD.name BEGIN
        UPDATE labels SET label=NEW.name WHERE activity_id=NEW.id AND participant_id=NEW.participant_id; END;
CREATE TRIGGER mt_activity_created AFTER INSERT ON activities BEGIN
        INSERT INTO activity_history(activity_id,participant_id,version,name,category,context,favorite,archived,changed_ms,origin)
        VALUES(NEW.id,NEW.participant_id,NEW.version,NEW.name,NEW.category,NEW.context,NEW.favorite,NEW.archived,NEW.updated_ms,NEW.origin); END;
CREATE TRIGGER mt_activity_updated AFTER UPDATE ON activities BEGIN
        INSERT INTO activity_history(activity_id,participant_id,version,name,category,context,favorite,archived,changed_ms,origin)
        VALUES(NEW.id,NEW.participant_id,NEW.version,NEW.name,NEW.category,NEW.context,NEW.favorite,NEW.archived,NEW.updated_ms,'edited'); END;
CREATE TRIGGER mt_activity_no_delete BEFORE DELETE ON activities BEGIN SELECT RAISE(ABORT,'Archive activities instead of deleting them'); END;
CREATE TRIGGER mt_label_created AFTER INSERT ON labels WHEN NEW.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history(label_id,participant_id,activity_id,label,recorded_label,note,start_ms,end_ms,source,reviewed,batch_id,action,changed_ms) VALUES(NEW.id,NEW.participant_id,NEW.activity_id,NEW.label,NEW.recorded_label,NEW.note,NEW.start_ms,NEW.end_ms,NEW.source,NEW.reviewed,NEW.batch_id,'created',CAST(strftime('%s','now') AS INTEGER)*1000); END;
CREATE TRIGGER mt_label_before_update BEFORE UPDATE ON labels WHEN OLD.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history(label_id,participant_id,activity_id,label,recorded_label,note,start_ms,end_ms,source,reviewed,batch_id,action,changed_ms) VALUES(OLD.id,OLD.participant_id,OLD.activity_id,OLD.label,OLD.recorded_label,OLD.note,OLD.start_ms,OLD.end_ms,OLD.source,OLD.reviewed,OLD.batch_id,'before-update',CAST(strftime('%s','now') AS INTEGER)*1000); END;
CREATE TRIGGER mt_label_updated AFTER UPDATE ON labels WHEN NEW.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history(label_id,participant_id,activity_id,label,recorded_label,note,start_ms,end_ms,source,reviewed,batch_id,action,changed_ms)
        VALUES(NEW.id,NEW.participant_id,NEW.activity_id,NEW.label,NEW.recorded_label,NEW.note,NEW.start_ms,NEW.end_ms,NEW.source,NEW.reviewed,NEW.batch_id,CASE WHEN OLD.activity_id IS NULL THEN 'created' ELSE 'updated' END,CAST(strftime('%s','now') AS INTEGER)*1000); END;
CREATE TRIGGER mt_label_deleted BEFORE DELETE ON labels WHEN OLD.activity_id IS NOT NULL BEGIN
        INSERT INTO activity_label_history(label_id,participant_id,activity_id,label,recorded_label,note,start_ms,end_ms,source,reviewed,batch_id,action,changed_ms) VALUES(OLD.id,OLD.participant_id,OLD.activity_id,OLD.label,OLD.recorded_label,OLD.note,OLD.start_ms,OLD.end_ms,OLD.source,OLD.reviewed,OLD.batch_id,'deleted',CAST(strftime('%s','now') AS INTEGER)*1000); END;
CREATE TRIGGER mt_activity_history_no_update BEFORE UPDATE ON activity_history BEGIN SELECT RAISE(ABORT,'Activity history is append only'); END;
CREATE TRIGGER mt_activity_history_no_delete BEFORE DELETE ON activity_history BEGIN SELECT RAISE(ABORT,'Activity history is append only'); END;
CREATE TRIGGER mt_activity_label_history_no_update BEFORE UPDATE ON activity_label_history BEGIN SELECT RAISE(ABORT,'Activity history is append only'); END;
CREATE TRIGGER mt_activity_label_history_no_delete BEFORE DELETE ON activity_label_history BEGIN SELECT RAISE(ABORT,'Activity history is append only'); END;
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('activity_label_history',4);
INSERT INTO "sqlite_sequence" VALUES('labels',2);
COMMIT;
