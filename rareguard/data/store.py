import json
import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS patient(pid TEXT PRIMARY KEY, name TEXT, dob TEXT);
CREATE TABLE IF NOT EXISTS lab(
  pid TEXT, date TEXT, code TEXT, value REAL,
  ref_low REAL, ref_high REAL, source TEXT,
  PRIMARY KEY(pid, date, code));
CREATE TABLE IF NOT EXISTS checkin(
  pid TEXT, date TEXT, temp REAL, symptoms TEXT,
  PRIMARY KEY(pid, date));
CREATE TABLE IF NOT EXISTS patient_owner(pid TEXT PRIMARY KEY, owner TEXT NOT NULL);
"""


class Store:
    """RareGuard 时序数据写入层（家庭端本地/院内单机部署）。"""

    def __init__(self, path: str):
        self.lock = threading.RLock()
        # FastAPI TestClient/uvicorn 在工作线程执行请求，需跨线程共享连接
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(_SCHEMA)

    def add_patient(self, pid, name, dob):
        self.conn.execute(
            "INSERT OR REPLACE INTO patient VALUES(?,?,?)", (pid, name, dob))
        self.conn.commit()

    def add_lab(self, pid, date, code, value, ref_low, ref_high, source="synth"):
        self.conn.execute(
            "INSERT OR REPLACE INTO lab VALUES(?,?,?,?,?,?,?)",
            (pid, date, code, value, ref_low, ref_high, source))
        self.conn.commit()

    def add_checkin(self, pid, date, temp, symptoms):
        self.conn.execute(
            "INSERT OR REPLACE INTO checkin VALUES(?,?,?,?)",
            (pid, date, temp, json.dumps(symptoms, ensure_ascii=False)))
        self.conn.commit()

    def rows(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def close(self):
        self.conn.close()

    def create_owned_patient(self, pid, name, dob, owner):
        """Atomic creation; never overwrite or claim an existing unowned record."""
        with self.lock, self.conn:
            if self.conn.execute("SELECT 1 FROM patient WHERE pid=?", (pid,)).fetchone():
                return False
            self.conn.execute("INSERT INTO patient VALUES(?,?,?)", (pid, name, dob))
            self.conn.execute("INSERT INTO patient_owner VALUES(?,?)", (pid, owner))
        return True

    def owns(self, pid, owner):
        with self.lock:
            return bool(self.conn.execute(
                "SELECT 1 FROM patient_owner WHERE pid=? AND owner=?", (pid, owner)
            ).fetchone())

    def export_patient(self, pid):
        with self.lock:
            patient = self.conn.execute("SELECT pid,name,dob FROM patient WHERE pid=?", (pid,)).fetchone()
            labs = self.conn.execute("SELECT date,code,value,ref_low,ref_high,source FROM lab WHERE pid=? ORDER BY date,code", (pid,)).fetchall()
            checkins = self.conn.execute("SELECT date,temp,symptoms FROM checkin WHERE pid=? ORDER BY date", (pid,)).fetchall()
        return {"patient": dict(zip(("pid", "name", "dob"), patient)),
                "labs": [dict(zip(("date", "code", "value", "ref_low", "ref_high", "source"), row)) for row in labs],
                "checkins": [{"date": d, "temp": t, "symptoms": json.loads(s)} for d,t,s in checkins]}

    def delete_owned_patient(self, pid, owner):
        with self.lock, self.conn:
            if not self.owns(pid, owner):
                return False
            for table in ("lab", "checkin", "patient_owner", "patient"):
                self.conn.execute(f"DELETE FROM {table} WHERE pid=?", (pid,))
        return True
