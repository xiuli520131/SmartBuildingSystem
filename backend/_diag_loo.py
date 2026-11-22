import sqlite3, numpy as np
from app.feature_service import decode_feature, distance, ALGORITHM, MIN_GAP

p = r"C:\SmartBuildingSystem\smart-access\data\smart_access.db"
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row

rows = c.execute("SELECT user_id, data FROM edge_samples WHERE role='train' AND algorithm=? ORDER BY user_id, id", (ALGORITHM,)).fetchall()
templates = [(r["user_id"], decode_feature(r["data"])) for r in rows]
thr = {r["user_id"]: r["threshold"] for r in c.execute("SELECT user_id, threshold FROM edge_thresholds")}

print(f"MIN_GAP={MIN_GAP}")
for i, (q_uid, q) in enumerate(templates):
    scores = []
    for t_uid, t in templates:
        if t_uid == q_uid:
            continue
        scores.append((distance(q, t), t_uid))
    own = [distance(q, t) for u, t in templates if u == q_uid]
    best_own = min(own)
    scores.sort()
    second = scores[0]
    gap = second[0] - best_own
    hit = "GAP_FAIL" if len(scores) > 1 and gap < MIN_GAP else ("THR_FAIL" if best_own > thr.get(q_uid, 1e9) else "OK")
    print(f"query uid={q_uid}#{i} own_best={best_own:.2f}(thr={thr.get(q_uid):.2f}) | nearest_other={second[1]}:{second[0]:.2f} gap={gap:.2f} -> {hit}")
c.close()
