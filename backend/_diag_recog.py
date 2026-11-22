import sqlite3, numpy as np, cv2
from app.feature_service import decode_feature, distance, ALGORITHM

p = r"C:\SmartBuildingSystem\smart-access\data\smart_access.db"
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row

print("=== users / samples / thresholds ===")
for u in c.execute("SELECT id, user_no, name, enabled FROM users ORDER BY id"):
    tr = c.execute("SELECT COUNT(*) FROM edge_samples WHERE user_id=? AND role='train'", (u["id"],)).fetchone()[0]
    va = c.execute("SELECT COUNT(*) FROM edge_samples WHERE user_id=? AND role='validation'", (u["id"],)).fetchone()[0]
    un = c.execute("SELECT COUNT(*) FROM edge_samples WHERE user_id=? AND role='unknown'", (u["id"],)).fetchone()[0]
    t = c.execute("SELECT threshold, enabled, demo FROM edge_thresholds WHERE user_id=?", (u["id"],)).fetchone()
    print(f"uid={u['id']} {u['user_no']} {u['name']} enabled={u['enabled']} train={tr} valid={va} unknown={un} threshold={dict(t) if t else None}")

print("\n=== recent 12 events ===")
for e in c.execute("""SELECT event_time, recognition, authorization, confidence, recognized_no, recognized_name, auth_reason, execution
                      FROM access_events ORDER BY id DESC LIMIT 12"""):
    print(dict(e))

print("\n=== train pairwise distances ===")
rows = c.execute("SELECT user_id, data FROM edge_samples WHERE role='train' AND algorithm=? ORDER BY user_id, id", (ALGORITHM,)).fetchall()
vecs = {}
for r in rows:
    vecs.setdefault(r["user_id"], []).append(decode_feature(r["data"]))
for uid, vs in vecs.items():
    if len(vs) >= 2:
        ds = [distance(a, b) for i, a in enumerate(vs) for b in vs[i+1:]]
        print(f"uid={uid} n={len(vs)} pairwise min={min(ds):.2f} max={max(ds):.2f} mean={sum(ds)/len(ds):.2f}")
    else:
        print(f"uid={uid} n={len(vs)} (single)")
# cross-user nearest
uids = list(vecs.keys())
if len(uids) >= 2:
    cross = []
    for a in vecs[uids[0]]:
        for b in vecs[uids[1]]:
            cross.append(distance(a, b))
    print(f"cross-user min={min(cross):.2f} mean={sum(cross)/len(cross):.2f}")
c.close()
