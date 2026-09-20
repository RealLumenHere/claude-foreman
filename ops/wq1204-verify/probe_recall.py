import sys, sqlite3, tempfile, os, datetime
sys.path.insert(0, "/home/lumen/mailbot")
import mail_recall as mr

tmp = tempfile.mkdtemp()
mr.DB = os.path.join(tmp, "t.db")
c = sqlite3.connect(mr.DB)
c.executescript(mr.SCHEMA)
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
c.execute("INSERT INTO threads VALUES ('t1','a@x.com','Al','quote?',?,?)", (now, now))
c.execute("INSERT INTO threads VALUES ('t2','b@x.com','Bo','hi',?,?)", (now, now))
c.execute("INSERT INTO messages (id,thread_id,direction,ts,from_addr,to_addr,body,probe,leak_flags,regenerated,needs_frank) VALUES ('m1','t1','in',?,'a@x.com','bot','need a quote',0,NULL,0,1)", (now,))
c.execute("INSERT INTO messages (id,thread_id,direction,ts,from_addr,to_addr,body,probe,leak_flags,regenerated,needs_frank) VALUES ('m2','t2','in',?,'b@x.com','bot','hello',0,NULL,0,0)", (now,))
c.commit()
mr.db = lambda: sqlite3.connect(mr.DB)
mr.needs_frank("7d")
