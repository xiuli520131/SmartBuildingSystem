import sqlite3
p = r"C:\SmartBuildingSystem\smart-access\data\smart_access.db"
c = sqlite3.connect(p)
c.row_factory = sqlite3.Row
print("=== sample source/role ===")
for r in c.execute("SELECT user_id, source, role, COUNT(*) n FROM edge_samples GROUP BY user_id, source, role ORDER BY user_id"):
    print(dict(r))
print("\n=== thresholds ===")
for r in c.execute("SELECT * FROM edge_thresholds"):
    print(dict(r))
c.close()
